"""Ollama lifecycle helpers for the Hardware settings tab: is it installed,
is it running, and (if installed but not running) starting it.

Installing Ollama itself is deliberately NOT automated here — same call
server.py's _bootstrap_dependencies() already makes for Node.js/npx: Ollama
is a system-level runtime with its own platform installer (a shell script
that uses sudo on Linux, a signed .app on macOS, an .exe on Windows), not a
Python package this app can pip-install. Silently invoking a root-requiring
installer in the background is exactly the kind of thing this app avoids
doing without you choosing to run it yourself — see hardware_probe.py's
dmidecode handling for the same reasoning applied to RAM-speed detection.
Instead, install_info() returns the right command/link for your OS so the
UI can show it, and start() covers the actually-safe, non-privileged part:
launching a daemon you already have installed.
"""

from __future__ import annotations

import asyncio
import platform
import shutil

from model_discovery import detect_ollama

_SYSTEM = platform.system()  # "Linux" | "Darwin" | "Windows"


def is_installed() -> bool:
    return shutil.which("ollama") is not None


def install_info() -> dict:
    """Platform-appropriate install instructions. Linux's official installer
    needs sudo (it places the binary in /usr/local/bin and sets up a
    systemd service), so it's shown as a command to run yourself rather
    than something this app executes for you. macOS/Windows ship a GUI
    installer instead, so those just link to the download."""
    if _SYSTEM == "Linux":
        return {
            "method": "command",
            "command": "curl -fsSL https://ollama.com/install.sh | sh",
            "note": "Official installer — it'll ask for your sudo password itself; run it in your own terminal.",
        }
    if _SYSTEM == "Darwin":
        return {
            "method": "download",
            "url": "https://ollama.com/download/mac",
            "note": "Or, if you use Homebrew: `brew install ollama` (no sudo needed).",
        }
    if _SYSTEM == "Windows":
        return {
            "method": "download",
            "url": "https://ollama.com/download/windows",
            "note": None,
        }
    return {"method": "download", "url": "https://ollama.com/download", "note": None}


async def start(ollama_base_url_override: str | None) -> tuple[bool, str]:
    """Launches `ollama serve` as a detached background process (no elevated
    privileges needed — same as running it from your own terminal) and
    waits briefly for it to come up. No-ops with a clear message if it's
    already running or not installed at all."""
    if not is_installed():
        return False, "Ollama isn't installed — see the install instructions above."

    if await detect_ollama(ollama_base_url_override):
        return True, "Already running."

    try:
        # start_new_session detaches it from this process group so it
        # keeps running independently of the starfire server, the same way
        # `ollama serve &` in a terminal would.
        subprocess_kwargs = {"stdout": asyncio.subprocess.DEVNULL, "stderr": asyncio.subprocess.DEVNULL}
        if _SYSTEM != "Windows":
            subprocess_kwargs["start_new_session"] = True
        await asyncio.create_subprocess_exec("ollama", "serve", **subprocess_kwargs)
    except OSError as e:
        return False, f"Couldn't launch `ollama serve`: {e}"

    # Poll for the daemon to actually come up rather than assuming success
    # the instant the process spawns — cold start can take a couple seconds.
    for _ in range(10):
        await asyncio.sleep(0.5)
        if await detect_ollama(ollama_base_url_override):
            return True, "Started."
    return False, "Launched `ollama serve`, but it hasn't come up yet — check back in a moment."
