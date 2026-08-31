"""Hardware-aware model suggestions: a scaled-down version of odysseus-dev's
Cookbook (which does full hardware detection, download management, and
serving via llama.cpp/vLLM/SGLang — real infrastructure this app's scope
doesn't need). This version detects RAM/VRAM capacity (does a model fit?),
CPU/GPU/RAM identity and speed (roughly, will it run well?), and suggests
which common Ollama-pullable models should actually work on this machine.

Cross-platform: Linux, macOS, and Windows each get their own detection
commands below (dispatched on platform.system()), since there's no single
tool that works everywhere for any of this. Only the Linux path has been
run and verified against a real machine as part of this app's own
development — the macOS/Windows commands are built against long-stable,
well-documented interfaces (sysctl, system_profiler, PowerShell's
Get-CimInstance) but haven't been executed on those OSes here. If one
returns something unexpected, every field it would have filled in just
stays None rather than guessing — same fallback philosophy as the Linux
path already had.
"""

from __future__ import annotations

import asyncio
import json
import platform
import re

_SYSTEM = platform.system()  # "Linux" | "Darwin" | "Windows"

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

# DMTF SMBIOS memory-type codes, Windows' WMI reports the same numbering —
# only the common modern ones; anything else is left untranslated as None.
_SMBIOS_MEMORY_TYPES = {20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 34: "DDR5"}


async def _run(cmd: list[str], timeout: float = 5.0) -> tuple[int, str]:
    """Shared subprocess-run-and-capture helper — one place for the
    timeout/decoding/graceful-failure behavior every probe below needs,
    instead of repeating it per platform per field."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (OSError, asyncio.TimeoutError):
        return -1, ""
    return proc.returncode, stdout.decode("utf-8", errors="replace")


async def _read_total_ram_gb() -> float | None:
    if _SYSTEM == "Linux":
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return round(int(line.split()[1]) / (1024 * 1024), 1)
        except (OSError, ValueError, IndexError):
            pass
        return None
    if _SYSTEM == "Darwin":
        code, out = await _run(["sysctl", "-n", "hw.memsize"])
        return round(int(out.strip()) / (1024 ** 3), 1) if code == 0 and out.strip().isdigit() else None
    if _SYSTEM == "Windows":
        code, out = await _run(["powershell", "-NoProfile", "-Command",
                                  "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"])
        return round(int(out.strip()) / (1024 ** 3), 1) if code == 0 and out.strip().isdigit() else None
    return None


async def _read_total_vram_gb() -> float | None:
    # nvidia-smi is the same tool/output shape on Linux and Windows for an
    # NVIDIA card; it's simply absent on Apple Silicon (no NVIDIA GPUs
    # exist there) and on any Mac/PC without a discrete NVIDIA GPU, which
    # _run() already turns into a clean "not found" rather than an error.
    code, text = await _run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"])
    if code != 0:
        return None
    # One line per GPU, MiB each — sum for multi-GPU setups.
    total_mib = sum(int(m.group(1)) for m in (re.match(r"\s*(\d+)", line) for line in text.splitlines()) if m)
    return round(total_mib / 1024, 1) if total_mib else None


async def _read_gpu_name() -> str | None:
    """The actual GPU model(s) (e.g. "NVIDIA GeForce RTX 4090") — knowing
    VRAM tells you whether a model *fits*; this is what tells you roughly
    how *fast* it'll run, since two cards with the same VRAM can have very
    different compute throughput. Deliberately not turned into a numeric
    speed score here — that would need a maintained benchmark table across
    hundreds of GPU models, which is a lot of upkeep for a rough estimate;
    showing the real model name lets you look it up yourself if you care to."""
    code, text = await _run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
    if code != 0:
        return None
    names = [line.strip() for line in text.splitlines() if line.strip()]
    return ", ".join(dict.fromkeys(names)) if names else None  # dedupe identical cards in a multi-GPU box


async def _read_cpu_info() -> dict:
    """Model name, logical core count, and max clock speed."""
    info = {"model": None, "cores": None, "max_mhz": None}
    if _SYSTEM == "Linux":
        return await _read_cpu_info_linux(info)
    if _SYSTEM == "Darwin":
        return await _read_cpu_info_macos(info)
    if _SYSTEM == "Windows":
        return await _read_cpu_info_windows(info)
    return info


async def _read_cpu_info_linux(info: dict) -> dict:
    # lscpu first (present on essentially every Linux install, no root
    # needed); /proc/cpuinfo as a fallback if it's somehow missing.
    code, text = await _run(["lscpu"])
    if code == 0:
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


async def _read_cpu_info_macos(info: dict) -> dict:
    code, out = await _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    if code == 0 and out.strip():
        info["model"] = out.strip()

    code, out = await _run(["sysctl", "-n", "hw.logicalcpu"])
    if code == 0 and out.strip().isdigit():
        info["cores"] = int(out.strip())

    # hw.cpufrequency_max only exists on Intel Macs — Apple Silicon doesn't
    # expose a clock speed this way at all, so this field just stays None
    # there rather than guessing at one.
    code, out = await _run(["sysctl", "-n", "hw.cpufrequency_max"])
    if code == 0 and out.strip().isdigit():
        info["max_mhz"] = round(int(out.strip()) / 1_000_000)
    return info


async def _read_cpu_info_windows(info: dict) -> dict:
    code, out = await _run(["powershell", "-NoProfile", "-Command",
        "Get-CimInstance Win32_Processor | Select-Object -First 1 Name,MaxClockSpeed,NumberOfLogicalProcessors | ConvertTo-Json"])
    if code != 0 or not out.strip():
        return info
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return info
    info["model"] = (data.get("Name") or "").strip() or None
    info["cores"] = data.get("NumberOfLogicalProcessors")
    info["max_mhz"] = data.get("MaxClockSpeed")
    return info


async def _read_ram_info() -> dict:
    """RAM type (DDR4/DDR5/...) and speed (MT/s or MHz, OS-reported)."""
    info = {"type": None, "speed_mts": None}
    if _SYSTEM == "Linux":
        return await _read_ram_info_linux(info)
    if _SYSTEM == "Darwin":
        return await _read_ram_info_macos(info)
    if _SYSTEM == "Windows":
        return await _read_ram_info_windows(info)
    return info


async def _read_ram_info_linux(info: dict) -> dict:
    """Needs `dmidecode` installed AND the whole process running as root —
    both are genuinely required on Linux (unlike macOS/Windows below, which
    can read this as an ordinary user); a normal invocation just gets
    permission-denied, treated here as "unknown" rather than an error."""
    code, text = await _run(["dmidecode", "-t", "memory"])
    if code != 0:
        return info
    for block in text.split("Memory Device"):
        type_match = re.search(r"^\s*Type:\s*(\S+)", block, re.MULTILINE)
        speed_match = re.search(r"^\s*Speed:\s*(\d+)\s*MT/s", block, re.MULTILINE)
        if type_match and speed_match and type_match.group(1) not in ("Unknown", "None"):
            info["type"] = type_match.group(1)
            info["speed_mts"] = int(speed_match.group(1))
            break
    return info


async def _read_ram_info_macos(info: dict) -> dict:
    """No root needed — system_profiler reads the same kind of SMBIOS-ish
    data dmidecode needs root for on Linux, but macOS exposes it to any
    user. Plain text output (not -json): the Type:/Speed: line format has
    been stable across macOS versions; the JSON schema has not always been
    as consistent between Intel and Apple Silicon Macs."""
    code, text = await _run(["system_profiler", "SPMemoryDataType"], timeout=10.0)
    if code != 0:
        return info
    type_match = re.search(r"^\s*Type:\s*(\S+)", text, re.MULTILINE)
    speed_match = re.search(r"^\s*Speed:\s*([\d.]+)\s*MHz", text, re.MULTILINE)
    if type_match:
        info["type"] = type_match.group(1)
    if speed_match:
        info["speed_mts"] = round(float(speed_match.group(1)))
    return info


async def _read_ram_info_windows(info: dict) -> dict:
    """No admin needed — reading hardware inventory via WMI/CIM is a
    normal-user operation on Windows."""
    code, out = await _run(["powershell", "-NoProfile", "-Command",
        "Get-CimInstance Win32_PhysicalMemory | Select-Object -First 1 Speed,SMBIOSMemoryType | ConvertTo-Json"])
    if code != 0 or not out.strip():
        return info
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return info
    info["speed_mts"] = data.get("Speed")
    info["type"] = _SMBIOS_MEMORY_TYPES.get(data.get("SMBIOSMemoryType"))
    return info


def _ram_speed_unavailable_hint() -> str:
    """Why RAM speed came back unknown differs by platform, so the hint
    does too — Linux is a real permissions wall; macOS/Windows shouldn't
    normally hit this at all."""
    if _SYSTEM == "Linux":
        return ("install dmidecode — `sudo apt install dmidecode` (Debian/Ubuntu) or "
                "`sudo dnf install dmidecode` (Fedora) — and note the app itself would need to run "
                "as root to actually read it, which this doesn't do automatically")
    if _SYSTEM == "Darwin":
        return "unexpected on macOS — this doesn't normally need elevated permissions; system_profiler may have returned an unfamiliar format"
    if _SYSTEM == "Windows":
        return "unexpected on Windows — this doesn't normally need admin rights; the WMI query may have returned an unfamiliar format"
    return "not supported on this OS"


def _speed_note(vram_gb: float | None, gpu_name: str | None, ram_info: dict) -> str:
    """A qualitative note, not a number — see _read_gpu_name()'s docstring
    for why this deliberately stops short of a per-GPU throughput estimate."""
    if vram_gb:
        gpu = f" ({gpu_name})" if gpu_name else ""
        return f"GPU inference{gpu} — a model that fits comfortably in VRAM should run smoothly; exact speed depends on your GPU's own compute throughput, which isn't estimated here."
    if ram_info["speed_mts"]:
        return (f"CPU/unified-memory inference — throughput is largely RAM-bandwidth-bound. "
                f"{ram_info['type'] or 'RAM'} at {ram_info['speed_mts']} MT/s gives a rough sense of that ceiling; "
                f"a discrete GPU would be meaningfully faster for any of these model sizes.")
    return (f"CPU/unified-memory inference — throughput is largely RAM-bandwidth-bound (couldn't detect your "
            f"RAM speed here: {_ram_speed_unavailable_hint()}); a discrete GPU would be meaningfully faster "
            f"for any of these model sizes.")


async def probe() -> dict:
    ram_gb = await _read_total_ram_gb()
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
