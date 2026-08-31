"""Web search: a single no-API-key backend (DuckDuckGo's HTML results page),
consistent with starfire's "no accounts needed" stance. odysseus-dev
aggregates six real search-provider APIs with ranking/caching/analytics
(services/search/) — real infrastructure this app's scope doesn't need.
Swapping this for a proper provider (Brave, SearXNG, Tavily) later is a
one-function change, since callers only ever see search() -> list[dict].

Scrapes DuckDuckGo's HTML (non-JS) endpoint rather than calling an official
API — it needs no key, but also carries no stability guarantee; if DDG
changes that page's markup this parser may need a matching update.
"""

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
    """Returns [{title, url, snippet}, ...]. Never raises — a request or
    parse failure comes back as an empty list, which callers treat as "no
    results" rather than an error worth surfacing to the model."""
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
