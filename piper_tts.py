"""Local speech generation via Piper — no API key, no account, and unlike
ComfyUI it's not even a server: `piper-tts` is a pip-installable package
(so server.py's dependency bootstrap can install it automatically, same as
uv) that runs as a one-shot CLI process per request, reading text on stdin
and writing a WAV file out.

Voices are the quality knob here, not a runtime parameter — Piper ships
each voice pre-baked in low/medium/high quality variants (different file
sizes/fidelity), so "quality" means "which voice variant you've downloaded
and configured," matching the ONNX model file you point this at.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import httpx

VALID_QUALITIES = {"low", "medium", "high"}
HF_VOICES_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
TIMEOUT = 60.0


def is_installed() -> bool:
    import shutil
    return shutil.which("piper") is not None


async def generate(text: str, voice_model_path: str) -> bytes:
    """Returns raw WAV bytes, or raises RuntimeError with a readable message."""
    if not is_installed():
        raise RuntimeError("piper is not installed — restart starfire.ai to trigger the automatic dependency "
                            "install, or run 'pip install piper-tts' yourself")
    if not os.path.isfile(voice_model_path):
        raise RuntimeError(f"voice model not found: {voice_model_path}")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        out_path = tmp.name
    try:
        proc = await asyncio.create_subprocess_exec(
            "piper", "--model", voice_model_path, "--output_file", out_path,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(text.encode("utf-8")), timeout=TIMEOUT)
        if proc.returncode != 0:
            raise RuntimeError(f"piper failed: {stderr.decode('utf-8', errors='replace')[:400]}")
        with open(out_path, "rb") as f:
            return f.read()
    except asyncio.TimeoutError as e:
        raise RuntimeError(f"piper timed out after {TIMEOUT:.0f}s") from e
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass


def voice_url(voice_name: str, quality: str) -> tuple[str, str]:
    """Returns (onnx_url, onnx_json_url) for a Piper voice on Hugging Face's
    rhasspy/piper-voices repo — e.g. voice_name='en_US-lessac',
    quality='medium' -> .../en/en_US/lessac/medium/en_US-lessac-medium.onnx.
    You still need to know a real voice name; this doesn't validate one
    exists, it just builds the conventional URL — the download itself will
    404 clearly if you got the name wrong."""
    if quality not in VALID_QUALITIES:
        quality = "medium"
    lang_region, speaker = voice_name.split("-", 1)
    lang = lang_region.split("_")[0]
    base = f"{HF_VOICES_BASE}/{lang}/{lang_region}/{speaker}/{quality}/{voice_name}-{quality}"
    return base + ".onnx", base + ".onnx.json"


async def pull_voice(voice_name: str, quality: str, dest_dir: str):
    """Downloads both files a Piper voice needs (.onnx + .onnx.json) into
    dest_dir. Yields (downloaded_bytes, total_bytes|None) for the .onnx
    file's progress (the .json sidecar is tiny, fetched without progress)."""
    onnx_url, json_url = voice_url(voice_name, quality)
    if not os.path.isdir(dest_dir):
        raise RuntimeError(f"voices directory does not exist: {dest_dir}")

    onnx_path = os.path.join(dest_dir, os.path.basename(onnx_url))
    json_path = os.path.join(dest_dir, os.path.basename(json_url))
    tmp_path = onnx_path + ".part"

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None), follow_redirects=True) as client:
        async with client.stream("GET", onnx_url) as r:
            r.raise_for_status()
            total = int(r.headers["content-length"]) if "content-length" in r.headers else None
            downloaded = 0
            try:
                with open(tmp_path, "wb") as f:
                    async for chunk in r.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
                        downloaded += len(chunk)
                        yield downloaded, total
            except BaseException:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                raise
        os.replace(tmp_path, onnx_path)

        r = await client.get(json_url)
        r.raise_for_status()
        with open(json_path, "wb") as f:
            f.write(r.content)
