"""Token-budget-based conversation trimming.

Ported idea (not code) from odysseus-dev's src/context_budget.py: budget to a
fraction of the model's context window rather than a fixed message count.
Scoped down hard — odysseus resolves a real per-model context-length registry
(model_capabilities.py); starfire has no such registry, so this uses the
context-window size the user already picks in the header's ctx-size selector
(sent with every request as options.num_ctx) as the budget input instead.

Token counts are estimated (~4 characters/token, a standard rough heuristic
for English text) rather than tokenized properly — good enough for a trim
decision, not for exact accounting. Replaces the old frontend behavior of
slicing to the last 20 messages regardless of their length, which could
still blow past a small model's context window on long messages, or waste
most of a large model's window on a short one.
"""

CHARS_PER_TOKEN = 4
RESPONSE_RESERVE_TOKENS = 512  # leave room for the model's own reply
MIN_BUDGET_TOKENS = 256


def estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // CHARS_PER_TOKEN)


def trim_to_budget(messages: list[dict], num_ctx: int) -> list[dict]:
    """Keep the most recent messages that fit inside num_ctx tokens (after
    reserving room for the response), always keeping at least the single
    most recent message so one very long message is never dropped entirely."""
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
