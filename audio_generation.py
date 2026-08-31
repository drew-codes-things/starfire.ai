"""Speech generation via OpenAI's TTS API — a real generated-audio upgrade
over the browser's own built-in Speech Synthesis (used for the chat "listen"
button), for when you specifically want an MP3 out of the conversation
rather than just hearing a reply read aloud in-browser.
"""

from __future__ import annotations

import httpx

TIMEOUT = 60.0
VALID_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}


async def generate(text: str, base_url: str, api_key: str, voice: str = "alloy") -> bytes:
    """Returns raw MP3 bytes, or raises RuntimeError with a readable message."""
    if voice not in VALID_VOICES:
        voice = "alloy"
    url = base_url.rstrip("/") + "/audio/speech"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"model": "tts-1", "input": text, "voice": voice}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"audio generation failed ({e.response.status_code}): {e.response.text[:300]}") from e
    except httpx.HTTPError as e:
        raise RuntimeError(f"audio generation request failed: {e}") from e
    return r.content
