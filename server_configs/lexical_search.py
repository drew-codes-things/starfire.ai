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
    q = tokenize(query)
    scored = [(item_id, score(q, tokenize(text))) for item_id, text in items]
    scored = [(item_id, s) for item_id, s in scored if s >= threshold]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [item_id for item_id, _ in scored[:max_items]]
