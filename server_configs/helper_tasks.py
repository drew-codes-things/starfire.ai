import re

THINKING_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
NO_THINK_MODELS = ("qwen3",)
HELPER_TEMPERATURE = 0.3
HELPER_MAX_TOKENS = 200


def ollama_endpoint(endpoints: list):
    return next((e for e in endpoints if e.provider == "ollama"), None)


def helper_think(model: str) -> bool | None:
    return False if model.lower().startswith(NO_THINK_MODELS) else None


def helper_options() -> dict:
    return {"temperature": HELPER_TEMPERATURE, "num_predict": HELPER_MAX_TOKENS}


def clean_reply(text: str) -> str:
    return THINKING_BLOCK.sub("", text).strip()
