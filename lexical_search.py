"""Shared token-overlap relevance scoring, used by memory_store.py and
documents_store.py. Ported from the scoring core of odysseus-dev's
src/memory.py:get_relevant_memories() — pure lexical (Jaccard-style token
overlap), no embeddings, no vector DB. Adequate at single-user scale (dozens
to low-thousands of memory entries / document chunks); a semantic layer can
be layered on later without changing this module's contract if it's ever
actually needed.
"""

import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def score(query_tokens: set[str], candidate_tokens: set[str]) -> float:
    if not query_tokens or not candidate_tokens:
        return 0.0
    overlap = query_tokens & candidate_tokens
    if not overlap:
        return 0.0
    return len(overlap) / len(query_tokens | candidate_tokens)


def rank(query: str, items: list[tuple[str, str]], threshold: float = 0.05,
         max_items: int = 8) -> list[str]:
    """items: [(id, text)]. Returns ids ranked by relevance, above threshold,
    highest first."""
    q = tokenize(query)
    scored = [(item_id, score(q, tokenize(text))) for item_id, text in items]
    scored = [(item_id, s) for item_id, s in scored if s >= threshold]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [item_id for item_id, _ in scored[:max_items]]
