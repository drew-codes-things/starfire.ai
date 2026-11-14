import json
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

import httpx

from atomic_io import atomic_write_json, holds_file_lock, report_unreadable_store
from config import config
from providers import _detect_provider, _ollama_api_root

logger = logging.getLogger(__name__)

LOCAL_HOSTS = ["127.0.0.1", "localhost"]
ARTY_ENGINE_PORT = 11438
OLLAMA_PORT = 11434
AUTO_DETECT_PORTS = [ARTY_ENGINE_PORT, OLLAMA_PORT]
DISCOVERY_PORTS = [ARTY_ENGINE_PORT, OLLAMA_PORT, 1234, 8080]
PROBE_TIMEOUT = 1.5
LOOPBACK_CONNECT_TIMEOUT = 0.5
AUTO_DETECT_TIMEOUT = httpx.Timeout(PROBE_TIMEOUT, connect=LOOPBACK_CONNECT_TIMEOUT)
OLLAMA_KIND = "ollama"
AUTO_DETECTED_SUFFIX = " (auto-detected)"
ARTY_ENGINE = "ArtyEngine"
OLLAMA = "Ollama"
ARTY_ENGINE_SERVER_PREFIX = "arty-engine/"
ARTY_ENGINE_GREETING = "Arty engine is running"
API_PATH_SUFFIX = "/api"


async def detect_ollama(env_override: str | None) -> str | None:
    if env_override:
        return env_override
    async with httpx.AsyncClient(timeout=AUTO_DETECT_TIMEOUT) as client:
        for port in AUTO_DETECT_PORTS:
            for host in LOCAL_HOSTS:
                url = f"http://{host}:{port}"
                if await _is_expected_backend_on(client, url, port):
                    return url
    return None


async def _is_expected_backend_on(client: httpx.AsyncClient, url: str, port: int) -> bool:
    backend = await _ollama_api_backend(client, url)
    if port == ARTY_ENGINE_PORT:
        return backend == ARTY_ENGINE
    return backend is not None


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
    def relabel(self, endpoint_id: str, label: str) -> bool:
        endpoints = self._load()
        for endpoint in endpoints:
            if endpoint.id == endpoint_id:
                endpoint.label = label
                self._save(endpoints)
                return True
        return False

    @holds_file_lock()
    def delete(self, endpoint_id: str) -> bool:
        endpoints = self._load()
        remaining = [e for e in endpoints if e.id != endpoint_id]
        if len(remaining) == len(endpoints):
            return False
        self._save(remaining)
        return True


def _auto_detected_label(backend: str) -> str:
    return f"{backend}{AUTO_DETECTED_SUFFIX}"


AUTO_DETECTED_LABELS = {_auto_detected_label(ARTY_ENGINE), _auto_detected_label(OLLAMA)}


def _is_on_this_machine(base_url: str) -> bool:
    return urlsplit(base_url).hostname in LOCAL_HOSTS


def stored_local_backend(store: ModelEndpointStore) -> ModelEndpoint | None:
    return next((e for e in store.list() if e.kind == OLLAMA_KIND and _is_on_this_machine(e.base_url)), None)


def chosen_backend_url(store: ModelEndpointStore, env_override: str | None) -> str | None:
    if env_override:
        return env_override
    endpoint = stored_local_backend(store)
    return endpoint.base_url if endpoint else None


def local_backend_url() -> str | None:
    return chosen_backend_url(ModelEndpointStore(config.data_dir), config.ollama_base_url)


def _relabel_if_auto_detected(store: ModelEndpointStore, endpoint: ModelEndpoint, backend: str | None) -> None:
    if backend is None or endpoint.label not in AUTO_DETECTED_LABELS:
        return
    label = _auto_detected_label(backend)
    if label != endpoint.label and store.relabel(endpoint.id, label):
        logger.info("renamed the endpoint at %s to %s", endpoint.base_url, label)


async def add_detected_backend(store: ModelEndpointStore, base_url: str) -> ModelEndpoint | None:
    backend = await identify_backend(base_url)
    existing = store.find_by_url(base_url)
    if existing:
        _relabel_if_auto_detected(store, existing, backend)
        return None
    return store.add(base_url, kind=OLLAMA_KIND, label=_auto_detected_label(backend or OLLAMA))


async def _confirm_stored_backend(store: ModelEndpointStore, stored: ModelEndpoint,
                                  start_ollama: Callable[[], Awaitable[bool]]) -> None:
    backend = await identify_backend(stored.base_url)
    if backend is None and await start_ollama():
        backend = await identify_backend(stored.base_url)
    _relabel_if_auto_detected(store, stored, backend)


async def _adopt_first_local_backend(store: ModelEndpointStore,
                                     start_ollama: Callable[[], Awaitable[bool]]) -> ModelEndpoint | None:
    detected = await detect_ollama(None)
    if not detected and await start_ollama():
        detected = await detect_ollama(None)
    return await add_detected_backend(store, detected) if detected else None


async def settle_local_backend(store: ModelEndpointStore, env_override: str | None,
                               start_ollama: Callable[[], Awaitable[bool]]) -> ModelEndpoint | None:
    if env_override:
        return await add_detected_backend(store, env_override)
    stored = stored_local_backend(store)
    if stored is None:
        return await _adopt_first_local_backend(store, start_ollama)
    await _confirm_stored_backend(store, stored, start_ollama)
    return None
