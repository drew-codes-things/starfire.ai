import httpx

LOCAL_HOSTS = ["127.0.0.1", "localhost"]

DISCOVERY_PORTS = [11434, 1234, 8080]

PROBE_TIMEOUT = 1.5

async def detect_ollama(env_override: str | None) -> str | None:
    if env_override:
        return env_override
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        for host in LOCAL_HOSTS:
            url = f"http://{host}:11434"
            try:
                r = await client.get(f"{url}/api/tags")
                if r.status_code == 200:
                    return url
            except httpx.HTTPError:
                continue
    return None

async def discover_servers() -> list[dict]:
    found: list[dict] = []
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        for host in LOCAL_HOSTS:
            for port in DISCOVERY_PORTS:
                base = f"http://{host}:{port}"
                try:
                    r = await client.get(f"{base}/api/tags")
                    if r.status_code == 200:
                        found.append({"base_url": base, "kind": "ollama", "label": f"Ollama ({host}:{port})"})
                        continue
                except httpx.HTTPError:
                    pass
                try:
                    r = await client.get(f"{base}/v1/models")
                    if r.status_code == 200:
                        found.append({"base_url": base, "kind": "openai-compatible", "label": f"Local server ({host}:{port})"})
                except httpx.HTTPError:
                    continue
    seen = set()
    deduped = []
    for item in found:
        key = item["base_url"].split("://", 1)[-1].split(":")[-1]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped
