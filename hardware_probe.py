"""Hardware-aware model suggestions: a scaled-down version of odysseus-dev's
Cookbook (which does full hardware detection, download management, and
serving via llama.cpp/vLLM/SGLang — real infrastructure this app's scope
doesn't need). This version does exactly one thing: detect how much RAM and
GPU VRAM this machine has, and suggest which common Ollama-pullable models
should actually fit and run well, instead of you guessing and hitting an
out-of-memory error or a model that swaps to disk and crawls.

Detection is stdlib + `nvidia-smi` only — no GPU vendor library dependency.
No VRAM detected (no `nvidia-smi`, e.g. Apple Silicon, AMD, or no discrete
GPU) falls back to sizing recommendations against system RAM for CPU/unified
-memory inference instead.
"""

from __future__ import annotations

import asyncio
import re

# (name, approximate size in GB at a typical Q4 quant — the size Ollama
# actually pulls by default). Not exhaustive; a deliberately short, current
# list of commonly-used general/coding models rather than trying to mirror
# Cookbook's full model catalog.
CANDIDATE_MODELS = [
    ("qwen2.5-coder:1.5b", 1.0),
    ("qwen2.5-coder:7b", 4.5),
    ("llama3.2:3b", 2.0),
    ("llama3.1:8b", 4.7),
    ("qwen2.5-coder:14b", 9.0),
    ("mistral:7b", 4.1),
    ("qwen2.5-coder:32b", 20.0),
    ("qwen3-coder:30b", 19.0),
    ("llama3.1:70b", 40.0),
]

# Rough overhead reserved for the OS/other apps before "usable for a model"
# — conservative on purpose so a suggestion doesn't leave a machine swapping.
_RAM_RESERVE_GB = 3.0
_VRAM_RESERVE_GB = 1.0


def _read_total_ram_gb() -> float | None:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return round(kb / (1024 * 1024), 1)
    except (OSError, ValueError, IndexError):
        pass
    return None


async def _read_total_vram_gb() -> float | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    except (OSError, asyncio.TimeoutError):
        return None
    if proc.returncode != 0:
        return None
    # One line per GPU, MiB each — sum for multi-GPU setups.
    total_mib = 0
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        match = re.match(r"\s*(\d+)", line)
        if match:
            total_mib += int(match.group(1))
    return round(total_mib / 1024, 1) if total_mib else None


async def _read_gpu_name() -> str | None:
    """The actual GPU model(s) (e.g. "NVIDIA GeForce RTX 4090") — knowing
    VRAM tells you whether a model *fits*; this is what tells you roughly
    how *fast* it'll run, since two cards with the same VRAM can have very
    different compute throughput. Deliberately not turned into a numeric
    speed score here — that would need a maintained benchmark table across
    hundreds of GPU models, which is a lot of upkeep for a rough estimate;
    showing the real model name lets you look it up yourself if you care to."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi", "--query-gpu=name", "--format=csv,noheader",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    except (OSError, asyncio.TimeoutError):
        return None
    if proc.returncode != 0:
        return None
    names = [line.strip() for line in stdout.decode("utf-8", errors="replace").splitlines() if line.strip()]
    return ", ".join(dict.fromkeys(names)) if names else None  # dedupe identical cards in a multi-GPU box


async def _read_cpu_info() -> dict:
    """Model name, logical core count, and max clock speed — via `lscpu`
    (present on essentially every Linux install, no root needed). Falls
    back to /proc/cpuinfo if lscpu isn't there; either path leaves a field
    as None rather than guessing at it."""
    info = {"model": None, "cores": None, "max_mhz": None}
    try:
        proc = await asyncio.create_subprocess_exec(
            "lscpu", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode == 0:
            text = stdout.decode("utf-8", errors="replace")
            for line in text.splitlines():
                if line.startswith("Model name:"):
                    info["model"] = line.split(":", 1)[1].strip()
                elif line.startswith("CPU(s):"):
                    try:
                        info["cores"] = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                elif line.startswith("CPU max MHz:"):
                    try:
                        info["max_mhz"] = round(float(line.split(":", 1)[1].strip()))
                    except ValueError:
                        pass
            if info["model"]:
                return info
    except (OSError, asyncio.TimeoutError):
        pass

    try:
        with open("/proc/cpuinfo") as f:
            text = f.read()
        names = re.findall(r"^model name\s*:\s*(.+)$", text, re.MULTILINE)
        if names:
            info["model"] = names[0].strip()
            info["cores"] = len(names)
        speeds = re.findall(r"^cpu MHz\s*:\s*([\d.]+)$", text, re.MULTILINE)
        if speeds:
            info["max_mhz"] = round(max(float(s) for s in speeds))
    except OSError:
        pass
    return info


async def _read_ram_info() -> dict:
    """RAM type (DDR4/DDR5/...) and speed (MT/s) via `dmidecode -t memory`.
    This almost always needs root on Linux — a normal user invocation just
    fails (permission denied), which this treats as "unknown", not an
    error worth surfacing loudly. Takes the first DIMM with a real
    (non-"Unknown") type/speed as representative rather than every slot."""
    info = {"type": None, "speed_mts": None}
    try:
        proc = await asyncio.create_subprocess_exec(
            "dmidecode", "-t", "memory",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
    except (OSError, asyncio.TimeoutError):
        return info
    if proc.returncode != 0:
        return info  # most commonly: not running as root

    text = stdout.decode("utf-8", errors="replace")
    for block in text.split("Memory Device"):
        type_match = re.search(r"^\s*Type:\s*(\S+)", block, re.MULTILINE)
        speed_match = re.search(r"^\s*Speed:\s*(\d+)\s*MT/s", block, re.MULTILINE)
        if type_match and speed_match and type_match.group(1) not in ("Unknown", "None"):
            info["type"] = type_match.group(1)
            info["speed_mts"] = int(speed_match.group(1))
            break
    return info


def _speed_note(vram_gb: float | None, gpu_name: str | None, ram_info: dict) -> str:
    """A qualitative note, not a number — see _read_gpu_name()'s docstring
    for why this deliberately stops short of a per-GPU throughput estimate."""
    if vram_gb:
        gpu = f" ({gpu_name})" if gpu_name else ""
        return f"GPU inference{gpu} — a model that fits comfortably in VRAM should run smoothly; exact speed depends on your GPU's own compute throughput, which isn't estimated here."
    if ram_info["speed_mts"]:
        return (f"CPU/unified-memory inference — throughput is largely RAM-bandwidth-bound. "
                f"{ram_info['type']} at {ram_info['speed_mts']} MT/s gives a rough sense of that ceiling; "
                f"a discrete GPU would be meaningfully faster for any of these model sizes.")
    return ("CPU/unified-memory inference — throughput is largely RAM-bandwidth-bound (couldn't detect your "
            "RAM speed here, which usually needs root/sudo); a discrete GPU would be meaningfully faster for "
            "any of these model sizes.")


async def probe() -> dict:
    ram_gb = _read_total_ram_gb()
    vram_gb = await _read_total_vram_gb()
    gpu_name = await _read_gpu_name() if vram_gb else None
    cpu_info = await _read_cpu_info()
    ram_info = await _read_ram_info()

    if vram_gb:
        budget = max(vram_gb - _VRAM_RESERVE_GB, 0)
        basis = "gpu"
    elif ram_gb:
        budget = max(ram_gb - _RAM_RESERVE_GB, 0)
        basis = "cpu"
    else:
        budget = 0
        basis = "unknown"

    recommended = [name for name, size_gb in CANDIDATE_MODELS if size_gb <= budget]
    # Always suggest at least the smallest candidate — better than an empty
    # list on a very constrained machine, with the caveat left to the caller.
    if not recommended and CANDIDATE_MODELS:
        recommended = [CANDIDATE_MODELS[0][0]]

    return {
        "ram_gb": ram_gb,
        "vram_gb": vram_gb,
        "gpu_name": gpu_name,
        "cpu_model": cpu_info["model"],
        "cpu_cores": cpu_info["cores"],
        "cpu_max_mhz": cpu_info["max_mhz"],
        "ram_type": ram_info["type"],
        "ram_speed_mts": ram_info["speed_mts"],
        "basis": basis,  # "gpu" | "cpu" | "unknown"
        "budget_gb": round(budget, 1),
        "speed_note": _speed_note(vram_gb, gpu_name, ram_info),
        "recommended": recommended,
        "all_candidates": [{"name": n, "size_gb": s, "fits": s <= budget} for n, s in CANDIDATE_MODELS],
    }
