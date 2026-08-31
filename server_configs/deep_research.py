from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

import web_search

MAX_SOURCES = 4
MAX_CHARS_PER_SOURCE = 3000
FETCH_TIMEOUT = 8.0

def _resolves_to_public_address(url: str) -> bool:
    try:
        host = urlparse(url).hostname
        if not host:
            return False
        for _family, _type, _proto, _canon, sockaddr in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                return False
        return True
    except (socket.gaierror, ValueError, UnicodeError):
        return False

async def _block_unsafe_redirects(request: httpx.Request) -> None:
    if not _resolves_to_public_address(str(request.url)):
        raise httpx.RequestError(f"blocked request to non-public address: {request.url}", request=request)

class _TextExtractor(HTMLParser):

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

def _extract_text(html: str) -> str:
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
    except Exception:
        pass
    text = " ".join(extractor.parts)
    return re.sub(r"\s+", " ", text).strip()

async def _fetch_page(client: httpx.AsyncClient, url: str) -> str:
    try:
        r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; starfire.ai)"})
        r.raise_for_status()
    except httpx.HTTPError:
        return ""
    return _extract_text(r.text)[:MAX_CHARS_PER_SOURCE]

async def research(query: str, chat_fn) -> str:
    results = await web_search.search(query, max_results=MAX_SOURCES)
    if not results:
        return "No search results found for that query - can't do research without sources."

    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True,
                                   event_hooks={"request": [_block_unsafe_redirects]}) as client:
        pages = [await _fetch_page(client, r["url"]) for r in results]

    sources_block = "\n\n".join(
        f"[{i + 1}] {r['title']} ({r['url']})\n{page or r['snippet']}"
        for i, (r, page) in enumerate(zip(results, pages))
    )

    prompt = (
        f"Research question: {query}\n\n"
        f"Here are {len(results)} sources gathered from the web:\n\n{sources_block}\n\n"
        "Write a clear, well-organized report answering the research question using only "
        "the sources above. Cite sources inline as [1], [2], etc. matching the numbers above. "
        "End with a 'Sources' list of the titles and URLs used. If the sources don't actually "
        "answer the question, say so plainly rather than guessing."
    )
    return await chat_fn(prompt)
