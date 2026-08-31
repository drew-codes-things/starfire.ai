"""Local model-server discovery: Ollama auto-detect + a small network scan.

Scoped down from odysseus-dev's src/model_discovery.py, which probes a much
wider port range and multiple discovery mechanisms (Tailscale, env host
lists, etc.). starfire only cares about the common local dev-server ports:
Ollama's default, LM Studio's default, and llama.cpp/vLLM's common default.

Detection is purely env-var override + localhost/port heuristics + active
HTTP probing — never a filesystem install-path check, matching odysseus.
"""

import httpx

LOCAL_HOSTS = ["127.0.0.1", "localhost"]

# 11434 = Ollama, 1234 = LM Studio, 8080 = llama.cpp/vLLM common default.
# Trimmed from odysseus's range(8000, 8021) + [8080, 1234, 11434, 11435].
DISCOVERY_PORTS = [11434, 1234, 8080]

PROBE_TIMEOUT = 1.5


async def detect_ollama(env_override: str | None) -> str | None:
    """Resolution order mirrors odysseus's /api/runtime default: env override
    first, then probe the two common localhost spellings."""
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
    """Broader scan for the "Scan network" button: probe each known local
    dev-server port for either an Ollama or an OpenAI-compatible /v1/models
    surface."""
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
    # Dedupe by base_url (both hosts resolve to the same box, so 127.0.0.1
    # and localhost hits on the same port would otherwise double up).
    seen = set()
    deduped = []
    for item in found:
        key = item["base_url"].split("://", 1)[-1].split(":")[-1]  # dedupe by port
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped
