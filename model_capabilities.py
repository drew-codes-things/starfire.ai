"""Tool-calling capability check — a scoped version of odysseus-dev's
model_capabilities.py (which tracks context length, vision, and tool
support from a real capability-metadata service). This version answers
exactly one question: does this model likely support tool/function
calling, so the UI can warn before you enable tools for a model that will
probably just ignore them (or error).

Hosted providers (OpenAI, Anthropic) are treated as tool-capable across the
board — virtually every current model on both is. Ollama is genuinely
mixed by model, so it's checked against a short, deliberately conservative
list of models KNOWN not to support tools; anything not matched is assumed
capable rather than guessed unsupported, since a false "probably fine" is
far less annoying than warning on every unrecognized model name.
"""

from __future__ import annotations

import re

# Name fragments of Ollama models known NOT to support tool-calling.
# Not exhaustive — new small/older models get added here as they come up,
# not derived from a live registry (there isn't one for Ollama).
_KNOWN_NON_TOOL_OLLAMA_PATTERNS = [
    "llama2", "tinyllama", "orca-mini", "phi:", "phi2", "vicuna", "wizard-vicuna",
    "gemma:2b", "gemma:7b", "codellama",
]

# Below this many billion parameters, a model can still accept Ollama's
# `tools` field and technically emit the tool-call format, but in practice
# is too weak to reliably decide *when* to use one — instead of just
# answering a plain question, it commonly hallucinates a bogus tool call
# (or echoes the tool-call JSON as visible text) rather than ignoring the
# tools it was given. Observed directly with qwen2.5-coder:1.5b answering
# "what's 9+10" with a fabricated memory-tool call. A practical usability
# cutoff, not a hard technical one — deliberately conservative so it only
# catches genuinely tiny models.
_MIN_RELIABLE_TOOL_PARAMS_B = 3.0

# Matches the "1.5b" / "3b" / "70b" parameter-size suffix Ollama tags
# conventionally carry (e.g. "qwen2.5-coder:1.5b").
_PARAM_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)b\b")


def _param_size_billions(model_id: str) -> float | None:
    match = _PARAM_SIZE_RE.search((model_id or "").lower())
    return float(match.group(1)) if match else None


def supports_tools(provider: str, model_id: str) -> bool:
    if provider in ("openai", "anthropic"):
        return True
    if provider == "ollama":
        model_lower = (model_id or "").lower()
        if any(pattern in model_lower for pattern in _KNOWN_NON_TOOL_OLLAMA_PATTERNS):
            return False
        size_b = _param_size_billions(model_lower)
        if size_b is not None and size_b < _MIN_RELIABLE_TOOL_PARAMS_B:
            return False
        return True
    return True  # unknown/custom OpenAI-compatible endpoint — assume capable
