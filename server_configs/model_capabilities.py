from __future__ import annotations

import re

_KNOWN_NON_TOOL_OLLAMA_PATTERNS = [
    "llama2", "tinyllama", "orca-mini", "phi:", "phi2", "vicuna", "wizard-vicuna",
    "gemma:2b", "gemma:7b", "codellama",
]

_MIN_RELIABLE_TOOL_PARAMS_B = 3.0
_FULL_TOOLSET_MIN_PARAMS_B = 12.0

TOOL_TIERS = ("none", "basic", "full")

BASIC_TIER_TOOLS = [
    "search_web", "search_documents", "manage_memory", "manage_notes",
    "find_image", "generate_image", "manage_tasks",
]
BASIC_TIER_MAX_TOOLS = 8

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
    return True

def tool_tier(provider: str, model_id: str) -> str:
    if not supports_tools(provider, model_id):
        return "none"
    if provider == "ollama":
        size_b = _param_size_billions(model_id)
        if size_b is not None and size_b < _FULL_TOOLSET_MIN_PARAMS_B:
            return "basic"
    return "full"

def tools_for_tier(tier: str, enabled_builtin_tools: set[str]) -> set[str]:
    if tier == "none":
        return set()
    if tier == "basic":
        return {name for name in enabled_builtin_tools if name in BASIC_TIER_TOOLS}
    return set(enabled_builtin_tools)

def tool_cap_for_tier(tier: str) -> int | None:
    return BASIC_TIER_MAX_TOOLS if tier == "basic" else None

def tier_description(tier: str) -> str:
    if tier == "none":
        return "no tools (model is too small or is known not to support tool-calling)"
    if tier == "basic":
        return (f"basic tool set (model under {_FULL_TOOLSET_MIN_PARAMS_B:g}B): "
                + ", ".join(BASIC_TIER_TOOLS) + f", at most {BASIC_TIER_MAX_TOOLS} tools in total")
    return "full tool set"
