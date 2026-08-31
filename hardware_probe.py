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


async def probe() -> dict:
    ram_gb = _read_total_ram_gb()
    vram_gb = await _read_total_vram_gb()

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
        "basis": basis,  # "gpu" | "cpu" | "unknown"
        "budget_gb": round(budget, 1),
        "recommended": recommended,
        "all_candidates": [{"name": n, "size_gb": s, "fits": s <= budget} for n, s in CANDIDATE_MODELS],
    }
