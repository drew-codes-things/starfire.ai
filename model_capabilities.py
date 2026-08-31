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

# Name fragments of Ollama models known NOT to support tool-calling.
# Not exhaustive — new small/older models get added here as they come up,
# not derived from a live registry (there isn't one for Ollama).
_KNOWN_NON_TOOL_OLLAMA_PATTERNS = [
    "llama2", "tinyllama", "orca-mini", "phi:", "phi2", "vicuna", "wizard-vicuna",
    "gemma:2b", "gemma:7b", "codellama",
]


def supports_tools(provider: str, model_id: str) -> bool:
    if provider in ("openai", "anthropic"):
        return True
    if provider == "ollama":
        model_lower = (model_id or "").lower()
        return not any(pattern in model_lower for pattern in _KNOWN_NON_TOOL_OLLAMA_PATTERNS)
    return True  # unknown/custom OpenAI-compatible endpoint — assume capable
