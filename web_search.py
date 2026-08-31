from __future__ import annotations

import re
from html import unescape

import httpx

_RESULT_RE = re.compile(
    r'<a rel="nofollow" class="result__a" href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
    r'<a[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")

def _strip_tags(html: str) -> str:
    return unescape(_TAG_RE.sub("", html)).strip()

async def search(query: str, max_results: int = 5) -> list[dict]:
    query = (query or "").strip()
    if not query:
        return []
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            r = await client.post(
                "https://html.duckduckgo.com/html/", data={"q": query},
                headers={"User-Agent": "Mozilla/5.0 (compatible; starfire.ai)"},
            )
        r.raise_for_status()
    except httpx.HTTPError:
        return []

    results = []
    for match in _RESULT_RE.finditer(r.text):
        results.append({
            "title": _strip_tags(match.group("title")),
            "url": match.group("url"),
            "snippet": _strip_tags(match.group("snippet")),
        })
        if len(results) >= max_results:
            break
    return results
