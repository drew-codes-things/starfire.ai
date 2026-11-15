from __future__ import annotations

import asyncio
import platform
import shutil

from model_endpoints import answering_ollama_address, ollama_serve_would_answer_at

_SYSTEM = platform.system()

def is_installed() -> bool:
    return shutil.which("ollama") is not None

def install_info() -> dict:
    if _SYSTEM == "Linux":
        return {
            "method": "command",
            "command": "curl -fsSL https://ollama.com/install.sh | sh",
            "note": "Official installer - it'll ask for your sudo password itself; run it in your own terminal.",
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

def _elsewhere_and_down(address: str) -> str:
    return (f"starfire uses the model server at {address}, which is not answering. Starting Ollama on this "
            "machine would not change that; start the server at that address instead.")


async def start(backend_address: str | None) -> tuple[bool, str]:
    if not is_installed():
        return False, "Ollama isn't installed - see the install instructions above."

    if await answering_ollama_address(backend_address):
        return True, "Already running."

    if not ollama_serve_would_answer_at(backend_address):
        return False, _elsewhere_and_down(backend_address)

    try:
        subprocess_kwargs = {"stdout": asyncio.subprocess.DEVNULL, "stderr": asyncio.subprocess.DEVNULL}
        if _SYSTEM != "Windows":
            subprocess_kwargs["start_new_session"] = True
        await asyncio.create_subprocess_exec("ollama", "serve", **subprocess_kwargs)
    except OSError as e:
        return False, f"Couldn't launch `ollama serve`: {e}"

    for _ in range(10):
        await asyncio.sleep(0.5)
        if await answering_ollama_address(backend_address):
            return True, "Started."
    return False, "Launched `ollama serve`, but it hasn't come up yet - check back in a moment."
