from __future__ import annotations

import base64

import httpx

TIMEOUT = 60.0

async def generate(prompt: str, base_url: str, api_key: str, size: str = "1024x1024") -> bytes:
    url = base_url.rstrip("/") + "/images/generations"
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {"model": "dall-e-3", "prompt": prompt, "n": 1, "size": size, "response_format": "b64_json"}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(url, headers=headers, json=payload)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"image generation failed ({e.response.status_code}): {e.response.text[:300]}") from e
    except httpx.HTTPError as e:
        raise RuntimeError(f"image generation request failed: {e}") from e

    data = r.json()
    try:
        b64 = data["data"][0]["b64_json"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"unexpected response shape from image API: {data}") from e
    return base64.b64decode(b64)
