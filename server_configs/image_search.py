from __future__ import annotations

import re

import httpx

from deep_research import _block_unsafe_redirects, _resolves_to_public_address

SEARCH_URL = "https://duckduckgo.com/"
RESULTS_URL = "https://duckduckgo.com/i.js"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_CANDIDATES = 5
IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}
_VQD_RE = re.compile(r"vqd=[\"']?([\d-]+)")


class ImageSearchError(Exception):
    pass


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:48] or "image"


async def search(query: str, max_results: int = 10, client: httpx.AsyncClient | None = None) -> list[dict]:
    query = (query or "").strip()
    if not query:
        return []
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=10.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    try:
        page = await client.get(SEARCH_URL, params={"q": query, "iax": "images", "ia": "images"})
        page.raise_for_status()
        match = _VQD_RE.search(page.text)
        if not match:
            raise ImageSearchError("image search is unavailable right now (no search token in the response)")
        listing = await client.get(
            RESULTS_URL,
            params={"l": "us-en", "o": "json", "q": query, "vqd": match.group(1), "f": ",,,", "p": "1"},
            headers={"Referer": "https://duckduckgo.com/"},
        )
        listing.raise_for_status()
        data = listing.json()
    except httpx.HTTPError as e:
        raise ImageSearchError(f"image search failed: {e}") from e
    except ValueError as e:
        raise ImageSearchError("image search returned something that is not JSON") from e
    finally:
        if own_client:
            await client.aclose()

    results = []
    for item in data.get("results", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict) or not item.get("image"):
            continue
        results.append({
            "title": str(item.get("title") or ""),
            "image_url": str(item["image"]),
            "thumbnail": str(item.get("thumbnail") or ""),
            "source": str(item.get("url") or ""),
            "width": item.get("width"),
            "height": item.get("height"),
        })
        if len(results) >= max_results:
            break
    return results


async def download(url: str, client: httpx.AsyncClient | None = None) -> tuple[bytes, str]:
    if not url.lower().startswith(("http://", "https://")):
        raise ImageSearchError("image links must start with http:// or https://")
    if not _resolves_to_public_address(url):
        raise ImageSearchError("that image is hosted on a non-public address")
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=15.0, follow_redirects=True, headers={"User-Agent": USER_AGENT},
                                   event_hooks={"request": [_block_unsafe_redirects]})
    try:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type not in IMAGE_TYPES:
                raise ImageSearchError(f"not an image ({content_type or 'unknown type'})")
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > MAX_IMAGE_BYTES:
                raise ImageSearchError("image is larger than 15 MB")
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    raise ImageSearchError("image is larger than 15 MB")
                chunks.append(chunk)
    except httpx.HTTPError as e:
        raise ImageSearchError(f"download failed: {e}") from e
    finally:
        if own_client:
            await client.aclose()
    data = b"".join(chunks)
    if not data:
        raise ImageSearchError("the image was empty")
    return data, content_type


async def find_and_download(query: str, index: int = 0) -> dict:
    results = await search(query)
    if not results:
        raise ImageSearchError(f"no images found for {query!r}")
    index = max(0, min(int(index or 0), len(results) - 1))
    problems: list[str] = []
    for item in results[index:index + MAX_CANDIDATES]:
        try:
            data, content_type = await download(item["image_url"])
        except ImageSearchError as e:
            problems.append(str(e))
            continue
        return {
            **item,
            "data": data,
            "content_type": content_type,
            "filename": _slug(query) + IMAGE_TYPES[content_type],
            "candidates": len(results),
        }
    raise ImageSearchError("could not download any of the top results: " + "; ".join(problems[:3]))
