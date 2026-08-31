"""Optional semantic search layer on top of the existing lexical search.

Uses Ollama's /api/embeddings endpoint when a local Ollama is reachable and
the requested embedding model is pulled — no new dependency, no forced API
key, consistent with starfire's "runs locally unless you opt in" stance.
Deliberately decoupled from which provider you're chatting with: memory and
document search always try the local Ollama for embeddings regardless of
whether you're currently talking to OpenAI/Anthropic/a remote endpoint,
since embedding is a separate concern from chat completion.

Falls back to lexical-only scoring (memory_store.py / documents_store.py's
prior, unchanged behavior) whenever Ollama is unreachable or the embedding
model isn't pulled — this can never make search worse or break the app for
someone who hasn't set up embeddings.
"""

from __future__ import annotations

import math

import httpx

DEFAULT_EMBED_MODEL = "nomic-embed-text"
_TIMEOUT = 5.0


async def embed(text: str, ollama_base_url: str, model: str = DEFAULT_EMBED_MODEL) -> list[float] | None:
    """Best-effort embedding via Ollama. None on any failure (Ollama not
    running, model not pulled, endpoint unreachable) — callers must treat
    that as "no semantic signal available", never as an error."""
    if not ollama_base_url or not (text or "").strip():
        return None
    url = ollama_base_url.rstrip("/") + "/api/embeddings"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(url, json={"model": model, "prompt": text})
        if r.status_code != 200:
            return None
        vector = r.json().get("embedding")
        return vector if isinstance(vector, list) and vector else None
    except httpx.HTTPError:
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _normalize(scores: dict[str, float]) -> dict[str, float]:
    """Min-max normalize to [0, 1] so lexical and semantic scores (different
    scales/distributions) can be averaged meaningfully. All-equal input maps
    to all-zero rather than dividing by zero."""
    if not scores:
        return {}
    lo, hi = min(scores.values()), max(scores.values())
    if hi - lo < 1e-9:
        return {k: 0.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


def blend_scores(lexical: dict[str, float], semantic: dict[str, float], semantic_weight: float = 0.5) -> dict[str, float]:
    """Combine two id->score maps (candidates may appear in only one of
    them — e.g. a candidate below the lexical threshold can still surface
    via a strong semantic match, and vice versa)."""
    lex_n = _normalize(lexical)
    sem_n = _normalize(semantic)
    ids = set(lex_n) | set(sem_n)
    return {
        item_id: (1 - semantic_weight) * lex_n.get(item_id, 0.0) + semantic_weight * sem_n.get(item_id, 0.0)
        for item_id in ids
    }
