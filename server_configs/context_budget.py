CHARS_PER_TOKEN = 4
RESPONSE_RESERVE_TOKENS = 512
MIN_BUDGET_TOKENS = 256

def estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // CHARS_PER_TOKEN)

def trim_to_budget(messages: list[dict], num_ctx: int) -> list[dict]:
    budget = max((num_ctx or 2048) - RESPONSE_RESERVE_TOKENS, MIN_BUDGET_TOKENS)
    kept: list[dict] = []
    used = 0
    for message in reversed(messages):
        cost = estimate_tokens(message.get("content") or "")
        if kept and used + cost > budget:
            break
        kept.append(message)
        used += cost
    kept.reverse()
    return kept
