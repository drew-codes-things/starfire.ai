import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass

import httpx

from atomic_io import atomic_write_json, holds_file_lock, report_unreadable_store
from providers import _detect_provider, _ollama_api_root

logger = logging.getLogger(__name__)

LOCAL_HOSTS = ["127.0.0.1", "localhost"]
ARTY_ENGINE_PORT = 11438
OLLAMA_PORT = 11434
AUTO_DETECT_PORTS = [ARTY_ENGINE_PORT, OLLAMA_PORT]
DISCOVERY_PORTS = [ARTY_ENGINE_PORT, OLLAMA_PORT, 1234, 8080]
PROBE_TIMEOUT = 1.5
ARTY_ENGINE = "ArtyEngine"
OLLAMA = "Ollama"
ARTY_ENGINE_SERVER_PREFIX = "arty-engine/"
ARTY_ENGINE_GREETING = "Arty engine is running"
API_PATH_SUFFIX = "/api"


async def detect_ollama(env_override: str | None) -> str | None:
    if env_override:
        return env_override
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        for port in AUTO_DETECT_PORTS:
            for host in LOCAL_HOSTS:
                url = f"http://{host}:{port}"
                try:
                    r = await client.get(f"{url}/api/tags")
                    if r.status_code == 200:
                        return url
                except httpx.HTTPError as error:
                    logger.debug("no Ollama API answered at %s: %r", url, error)
                    continue
    return None


def _server_header_names_arty_engine(response: httpx.Response) -> bool:
    return any(value.strip().lower().startswith(ARTY_ENGINE_SERVER_PREFIX)
               for value in response.headers.get_list("server"))


async def _greets_as_arty_engine(client: httpx.AsyncClient, server_root: str) -> bool:
    try:
        r = await client.get(f"{server_root}/")
    except httpx.HTTPError as error:
        logger.debug("no greeting from %s: %r", server_root, error)
        return False
    return r.status_code == 200 and r.text.startswith(ARTY_ENGINE_GREETING)


async def _ollama_api_backend(client: httpx.AsyncClient, base_url: str) -> str | None:
    api_root = _ollama_api_root(base_url)
    try:
        r = await client.get(f"{api_root}/tags")
    except httpx.HTTPError as error:
        logger.debug("no Ollama API at %s: %r", base_url, error)
        return None
    if r.status_code != 200:
        return None
    if _server_header_names_arty_engine(r):
        return ARTY_ENGINE
    if await _greets_as_arty_engine(client, api_root.removesuffix(API_PATH_SUFFIX)):
        return ARTY_ENGINE
    return OLLAMA


async def identify_backend(base_url: str) -> str | None:
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        return await _ollama_api_backend(client, base_url)


async def discover_servers() -> list[dict]:
    found: list[dict] = []
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        for host in LOCAL_HOSTS:
            for port in DISCOVERY_PORTS:
                base = f"http://{host}:{port}"
                backend = await _ollama_api_backend(client, base)
                if backend:
                    found.append({"base_url": base, "kind": "ollama", "label": f"{backend} ({host}:{port})"})
                    continue
                try:
                    r = await client.get(f"{base}/v1/models")
                    if r.status_code == 200:
                        found.append({"base_url": base, "kind": "openai-compatible", "label": f"Local server ({host}:{port})"})
                except httpx.HTTPError as error:
                    logger.debug("no OpenAI-compatible API at %s: %r", base, error)
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

@dataclass
class ModelEndpoint:
    id: str
    base_url: str
    kind: str
    provider: str
    label: str = ""
    model_type: str = "chat"

class ModelEndpointStore:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "endpoints.json")

    def _load(self) -> list[ModelEndpoint]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as error:
            report_unreadable_store(self.path, error)
            return []
        if not isinstance(raw, list):
            return []
        endpoints = []
        for item in raw:
            if not isinstance(item, dict) or "id" not in item or "base_url" not in item:
                continue
            endpoints.append(ModelEndpoint(
                id=item["id"],
                base_url=item["base_url"],
                kind=item.get("kind", "api-key"),
                provider=item.get("provider") or _detect_provider(item["base_url"]),
                label=item.get("label", ""),
                model_type=item.get("model_type", "chat"),
            ))
        return endpoints

    def _save(self, endpoints: list[ModelEndpoint]) -> None:
        atomic_write_json(self.path, [asdict(e) for e in endpoints])

    def list(self) -> list[ModelEndpoint]:
        return self._load()

    def get(self, endpoint_id: str) -> ModelEndpoint | None:
        for e in self._load():
            if e.id == endpoint_id:
                return e
        return None

    def find_by_url(self, base_url: str) -> ModelEndpoint | None:
        for e in self._load():
            if e.base_url.rstrip("/") == base_url.rstrip("/"):
                return e
        return None

    @holds_file_lock()
    def add(self, base_url: str, kind: str, label: str = "", model_type: str = "chat") -> ModelEndpoint:
        endpoints = self._load()
        endpoint = ModelEndpoint(
            id=uuid.uuid4().hex[:12],
            base_url=base_url.rstrip("/"),
            kind=kind,
            provider=_detect_provider(base_url),
            label=label,
            model_type=model_type,
        )
        endpoints.append(endpoint)
        self._save(endpoints)
        return endpoint

    @holds_file_lock()
    def delete(self, endpoint_id: str) -> bool:
        endpoints = self._load()
        remaining = [e for e in endpoints if e.id != endpoint_id]
        if len(remaining) == len(endpoints):
            return False
        self._save(remaining)
        return True


async def add_detected_backend(store: ModelEndpointStore, base_url: str) -> ModelEndpoint | None:
    if store.find_by_url(base_url):
        return None
    backend = await identify_backend(base_url) or OLLAMA
    return store.add(base_url, kind="ollama", label=f"{backend} (auto-detected)")
