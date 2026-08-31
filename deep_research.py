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

import re
from html.parser import HTMLParser

import httpx

import web_search

MAX_SOURCES = 4
MAX_CHARS_PER_SOURCE = 3000
FETCH_TIMEOUT = 8.0


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

    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True) as client:
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
