"""Deep Research: a scoped version of odysseus-dev's multi-step research
agent (src/deep_research.py + services/research/). Odysseus's version is a
whole iterative search -> read -> re-search -> synthesize loop with its own
service layer; this version does one research pass rather than an open-ended
loop: search the web, fetch and read the top results, then ask the chat
model to synthesize a cited report from those excerpts in a single call.
Exposed as one builtin tool (`deep_research`) rather than a separate UI
surface, so it composes with everything tool-calling already does (MCP
servers, other builtin tools) instead of duplicating the chat UI.
"""

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
    """SSRF guard: reject a URL whose host resolves to a private/loopback/
    link-local/reserved address (RFC1918 ranges, 169.254.169.254 cloud
    metadata, localhost, etc.). This tool fetches URLs automatically with no
    user review in between — search results are external content this app
    doesn't control, and with follow_redirects on, even an initially
    external-looking URL could redirect into the home network this app
    typically runs on. Checked as an httpx request event hook (below) so it
    re-validates every redirect hop, not just the first request. Not
    airtight against a DNS-rebind between this check and the actual
    connection — that would need pinning the resolved IP on the connection
    itself — but it closes the ordinary case.
    """
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
    """Strips tags and script/style content, same approach as
    email_client.py's own HTML stripper, kept local here since document
    fetches (arbitrary web pages) are a different trust context from email
    bodies and this module has no other reason to import email_client."""

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
    """chat_fn(prompt: str) -> awaitable[str] — a single non-streaming chat
    call, e.g. agent_loop.run_chat_collected bound to the caller's endpoint
    and model. Returns the synthesized report text (already includes its
    own source list, since that's part of what the model is asked to
    produce)."""
    results = await web_search.search(query, max_results=MAX_SOURCES)
    if not results:
        return "No search results found for that query — can't do research without sources."

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
