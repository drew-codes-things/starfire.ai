import re

THINKING_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
THINKING_FAMILY = re.compile(r"^qwen3(?![0-9])", re.IGNORECASE)
HELPER_TEMPERATURE = 0.3
HELPER_MAX_TOKENS = 200
THINKING_HELPER_MAX_TOKENS = 1024


def ollama_endpoint(endpoints: list):
    return next((e for e in endpoints if e.provider == "ollama"), None)


def model_name_part(model: str) -> str:
    return model.rsplit("/", 1)[-1]


def thinks_by_default(model: str) -> bool:
    return bool(THINKING_FAMILY.match(model_name_part(model)))


def helper_think(model: str) -> bool | None:
    return False if thinks_by_default(model) else None


def helper_options(model: str) -> dict:
    tokens = THINKING_HELPER_MAX_TOKENS if thinks_by_default(model) else HELPER_MAX_TOKENS
    return {"temperature": HELPER_TEMPERATURE, "num_predict": tokens}


def clean_reply(text: str) -> str:
    return THINKING_BLOCK.sub("", text).strip()
