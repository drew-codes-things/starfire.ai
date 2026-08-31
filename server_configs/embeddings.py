from __future__ import annotations

import math

import httpx

DEFAULT_EMBED_MODEL = "nomic-embed-text"
_TIMEOUT = 5.0

async def embed(text: str, ollama_base_url: str, model: str = DEFAULT_EMBED_MODEL) -> list[float] | None:
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
    if not scores:
        return {}
    lo, hi = min(scores.values()), max(scores.values())
    if hi - lo < 1e-9:
        return {k: 0.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}

def blend_scores(lexical: dict[str, float], semantic: dict[str, float], semantic_weight: float = 0.5) -> dict[str, float]:
    lex_n = _normalize(lexical)
    sem_n = _normalize(semantic)
    ids = set(lex_n) | set(sem_n)
    return {
        item_id: (1 - semantic_weight) * lex_n.get(item_id, 0.0) + semantic_weight * sem_n.get(item_id, 0.0)
        for item_id in ids
    }
