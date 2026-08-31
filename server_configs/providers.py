from urllib.parse import urlparse

def _host_match(url: str, *domains: str) -> bool:
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    return bool(host) and any(host == d or host.endswith("." + d) for d in domains)

def _is_ollama_native_url(url: str) -> bool:
    try:
        parsed = urlparse(url or "")
    except Exception:
        return False
    host = parsed.hostname or ""
    path = (parsed.path or "").rstrip("/")
    if _host_match(url, "ollama.com"):
        return True
    if path.startswith("/v1"):
        return False
    local_ollama_host = host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or parsed.port == 11434
    return local_ollama_host and (path == "" or path == "/api" or path.startswith("/api/"))

def _detect_provider(url: str) -> str:
    if _is_ollama_native_url(url):
        return "ollama"
    if _host_match(url, "anthropic.com"):
        return "anthropic"
    return "openai"

def _ollama_api_root(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    parsed = urlparse(url)
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/api/chat"):
        return url[: -len("/chat")]
    if path.endswith("/api/tags"):
        return url[: -len("/tags")]
    if path.endswith("/api/generate"):
        return url[: -len("/generate")]
    if path.endswith("/api"):
        return url
    if path == "":
        if _host_match(url, "ollama.com"):
            root = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "https://ollama.com"
            return root.rstrip("/") + "/api"
        return url + "/api"
    return url

def build_chat_url(base: str) -> str:
    base = (base or "").strip().rstrip("/")
    provider = _detect_provider(base)
    if provider == "anthropic":
        return base + "/v1/messages"
    if provider == "ollama":
        return _ollama_api_root(base) + "/chat"
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1") or "/v1/" in base:
        return base + "/chat/completions"
    return base + "/v1/chat/completions"

def build_models_url(base: str) -> str:
    base = (base or "").strip().rstrip("/")
    provider = _detect_provider(base)
    if provider == "anthropic":
        return base + "/v1/models"
    if provider == "ollama":
        return _ollama_api_root(base) + "/tags"
    if base.endswith("/models"):
        return base
    if base.endswith("/v1") or "/v1/" in base:
        return base + "/models"
    return base + "/v1/models"

def build_headers(api_key: str | None, base: str) -> dict:
    provider = _detect_provider(base)
    headers: dict = {}
    if provider == "anthropic":
        if api_key:
            headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
        return headers
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers

def build_tools_param(tools: list[dict], base: str) -> list[dict]:
    if not tools:
        return []
    provider = _detect_provider(base)
    if provider != "anthropic":
        return tools
    converted = []
    for t in tools:
        fn = t.get("function", t)
        converted.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return converted
