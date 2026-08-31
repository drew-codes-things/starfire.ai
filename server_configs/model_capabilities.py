from __future__ import annotations

import re

_KNOWN_NON_TOOL_OLLAMA_PATTERNS = [
    "llama2", "tinyllama", "orca-mini", "phi:", "phi2", "vicuna", "wizard-vicuna",
    "gemma:2b", "gemma:7b", "codellama",
]

_MIN_RELIABLE_TOOL_PARAMS_B = 3.0

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
