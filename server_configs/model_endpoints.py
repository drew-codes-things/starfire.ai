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
from providers import THIS_MACHINE_HOSTS, _detect_provider, _host_match, _ollama_api_root

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
OPENAI_COMPATIBLE_KIND = "openai-compatible"
AUTO_DETECTED_SUFFIX = " (auto-detected)"
ARTY_ENGINE = "ArtyEngine"
OLLAMA = "Ollama"
ARTY_ENGINE_SERVER_PREFIX = "arty-engine/"
ARTY_ENGINE_GREETING = "Arty engine is running"
API_PATH_SUFFIX = "/api"
OPENAI_PATH_SUFFIX = "/v1"
OLLAMA_PROVIDER = "ollama"
OLLAMA_CLOUD_DOMAIN = "ollama.com"
LOCAL_SERVER_KINDS = (OLLAMA_KIND, OPENAI_COMPATIBLE_KIND)
OK = 200
NOT_FOUND = 404
TAGS_ANSWERS_WITHOUT_THE_OLLAMA_API = (OK, NOT_FOUND)
OLLAMA_MODELS_KEY = "models"
OPENAI_MODELS_KEY = "data"


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
    if not lists_ollama_models(r):
        return None
    if _server_header_names_arty_engine(r):
        return ARTY_ENGINE
    if await _greets_as_arty_engine(client, api_root.removesuffix(API_PATH_SUFFIX)):
        return ARTY_ENGINE
    return OLLAMA


async def identify_backend(base_url: str) -> str | None:
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        return await _ollama_api_backend(client, base_url)


def ollama_tags_url(base_url: str) -> str:
    api_root = _ollama_api_root(base_url)
    if api_root.endswith(API_PATH_SUFFIX):
        return f"{api_root}/tags"
    return f"{api_root.removesuffix(OPENAI_PATH_SUFFIX)}{API_PATH_SUFFIX}/tags"


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
                    if _lists_openai_models(r):
                        found.append({"base_url": f"{base}{OPENAI_PATH_SUFFIX}", "kind": OPENAI_COMPATIBLE_KIND,
                                      "label": f"Local server ({host}:{port})"})
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
    added_as: str = ""

    def __post_init__(self) -> None:
        self.added_as = self.added_as or self.kind

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
                added_as=item.get("added_as", ""),
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
    def change_kind(self, endpoint_id: str, kind: str) -> bool:
        endpoints = self._load()
        for endpoint in endpoints:
            if endpoint.id == endpoint_id:
                endpoint.kind = kind
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
    try:
        return urlsplit(base_url).hostname in THIS_MACHINE_HOSTS
    except ValueError:
        return False


def _server_root(base_url: str) -> str:
    return _ollama_api_root(base_url).removesuffix(API_PATH_SUFFIX).removesuffix(OPENAI_PATH_SUFFIX)


def stored_local_backend(store: ModelEndpointStore) -> ModelEndpoint | None:
    return next((e for e in store.list() if e.kind == OLLAMA_KIND and _is_on_this_machine(e.base_url)), None)


def _is_set_up_ollama_api_server(endpoint: ModelEndpoint) -> bool:
    if endpoint.kind == OLLAMA_KIND:
        return True
    return _is_ollama_server_elsewhere_added_with_a_key(endpoint)


def _is_ollama_server_elsewhere_added_with_a_key(endpoint: ModelEndpoint) -> bool:
    added_with_a_key = endpoint.kind not in LOCAL_SERVER_KINDS
    is_ollama_cloud = _host_match(endpoint.base_url, OLLAMA_CLOUD_DOMAIN)
    return (added_with_a_key and endpoint.provider == OLLAMA_PROVIDER and not is_ollama_cloud
            and not _is_on_this_machine(endpoint.base_url))


def _set_up_ollama_api_servers(store: ModelEndpointStore) -> list[ModelEndpoint]:
    return [e for e in store.list() if _is_set_up_ollama_api_server(e)]


def _local_model_servers(store: ModelEndpointStore) -> list[ModelEndpoint]:
    return [e for e in store.list() if e.kind in LOCAL_SERVER_KINDS and _is_on_this_machine(e.base_url)]


def chosen_backend_url(store: ModelEndpointStore, env_override: str | None) -> str | None:
    if env_override:
        return _server_root(env_override)
    endpoint = stored_local_backend(store)
    return _server_root(endpoint.base_url) if endpoint else None


def local_backend_url() -> str | None:
    return chosen_backend_url(ModelEndpointStore(config.data_dir), config.ollama_base_url)


def _relabel_if_auto_detected(store: ModelEndpointStore, endpoint: ModelEndpoint, backend: str | None) -> None:
    if backend is None or endpoint.label not in AUTO_DETECTED_LABELS:
        return
    label = _auto_detected_label(backend)
    if label != endpoint.label and store.relabel(endpoint.id, label):
        logger.info("renamed the endpoint at %s to %s", endpoint.base_url, label)


def _same_server_on_this_machine(first: str, second: str) -> bool:
    try:
        one, other = urlsplit(_server_root(first)), urlsplit(_server_root(second))
        return (one.hostname in THIS_MACHINE_HOSTS and other.hostname in THIS_MACHINE_HOSTS
                and (one.scheme, one.port, one.path) == (other.scheme, other.port, other.path))
    except ValueError:
        return False


def _may_serve_as_ollama(endpoint: ModelEndpoint) -> bool:
    return endpoint.kind == OLLAMA_KIND or _may_be_filed_as(endpoint, OLLAMA_KIND)


def _stored_under_another_local_spelling(store: ModelEndpointStore, base_url: str) -> ModelEndpoint | None:
    return next((e for e in store.list() if _same_server_on_this_machine(e.base_url, base_url) and _may_serve_as_ollama(e)),
                None)


async def add_detected_backend(store: ModelEndpointStore, base_url: str) -> ModelEndpoint | None:
    backend = await identify_backend(base_url)
    existing = store.find_by_url(base_url) or _stored_under_another_local_spelling(store, base_url)
    if existing:
        _relabel_if_auto_detected(store, existing, backend)
        _file_back_as_ollama_if_it_answers(store, existing, backend)
        return None
    return store.add(base_url, kind=OLLAMA_KIND, label=_auto_detected_label(backend or OLLAMA))


def _added_as_an_openai_server(endpoint: ModelEndpoint) -> bool:
    return endpoint.added_as != OLLAMA_KIND and endpoint.base_url.rstrip("/").endswith(OPENAI_PATH_SUFFIX)


def _may_be_filed_as(endpoint: ModelEndpoint, kind: str | None) -> bool:
    if kind is None or kind == endpoint.kind:
        return False
    return kind != OLLAMA_KIND or not _added_as_an_openai_server(endpoint)


def _file_back_as_ollama_if_it_answers(store: ModelEndpointStore, endpoint: ModelEndpoint, backend: str | None) -> None:
    if backend is not None and _may_be_filed_as(endpoint, OLLAMA_KIND) and store.change_kind(endpoint.id, OLLAMA_KIND):
        logger.info("the endpoint at %s speaks the Ollama API; it is filed as %s again", endpoint.base_url, OLLAMA_KIND)


async def _confirm_stored_backend(store: ModelEndpointStore, stored: ModelEndpoint,
                                  start_ollama: Callable[[str | None], Awaitable[bool]]) -> None:
    root = _server_root(stored.base_url)
    backend = await identify_backend(root)
    if backend is None and await start_ollama(root):
        backend = await identify_backend(root)
    _relabel_if_auto_detected(store, stored, backend)


async def _adopt_first_local_backend(store: ModelEndpointStore,
                                     start_ollama: Callable[[str | None], Awaitable[bool]]) -> ModelEndpoint | None:
    detected = await detect_ollama(None)
    if not detected and await start_ollama(None):
        detected = await detect_ollama(None)
    return await add_detected_backend(store, detected) if detected else None


def _lists_models_under(response: httpx.Response, key: str) -> bool:
    if response.status_code != OK:
        return False
    try:
        return isinstance(response.json().get(key), list)
    except (ValueError, AttributeError):
        return False


def lists_ollama_models(response: httpx.Response) -> bool:
    return _lists_models_under(response, OLLAMA_MODELS_KEY)


def _lists_openai_models(response: httpx.Response) -> bool:
    return _lists_models_under(response, OPENAI_MODELS_KEY)


async def _api_spoken_by(client: httpx.AsyncClient, base_url: str) -> str | None:
    root = _server_root(base_url)
    try:
        tags = await client.get(f"{root}{API_PATH_SUFFIX}/tags")
        if lists_ollama_models(tags):
            return OLLAMA_KIND
        if tags.status_code not in TAGS_ANSWERS_WITHOUT_THE_OLLAMA_API:
            return None
        models = await client.get(f"{root}{OPENAI_PATH_SUFFIX}/models")
    except httpx.HTTPError as error:
        logger.debug("could not tell which API %s speaks: %r", base_url, error)
        return None
    return OPENAI_COMPATIBLE_KIND if _lists_openai_models(models) else None


async def _file_local_servers_by_the_api_they_speak(store: ModelEndpointStore) -> None:
    local = _local_model_servers(store)
    if not local:
        return
    async with httpx.AsyncClient(timeout=AUTO_DETECT_TIMEOUT) as client:
        for endpoint in local:
            kind = await _api_spoken_by(client, endpoint.base_url)
            if _may_be_filed_by_the_api_it_speaks(store, endpoint, kind) and store.change_kind(endpoint.id, kind):
                logger.info("the endpoint at %s is now filed as %s, the API it speaks", endpoint.base_url, kind)


def _may_be_filed_by_the_api_it_speaks(store: ModelEndpointStore, endpoint: ModelEndpoint, kind: str | None) -> bool:
    takes_over_from_the_chosen_server = kind == OLLAMA_KIND and _another_server_is_chosen(store, endpoint)
    return not takes_over_from_the_chosen_server and _may_be_filed_as(endpoint, kind)


def _ollama_added_with_a_key_on_this_machine(store: ModelEndpointStore) -> list[ModelEndpoint]:
    return [e for e in store.list()
            if e.kind not in LOCAL_SERVER_KINDS and e.provider == OLLAMA_PROVIDER and _is_on_this_machine(e.base_url)]


def _another_server_is_chosen(store: ModelEndpointStore, endpoint: ModelEndpoint) -> bool:
    chosen = stored_local_backend(store)
    return chosen is not None and not _same_server_on_this_machine(chosen.base_url, endpoint.base_url)


async def _file_key_added_ollama_on_this_machine_that_answers(store: ModelEndpointStore) -> None:
    key_added = _ollama_added_with_a_key_on_this_machine(store)
    if not key_added:
        return
    async with httpx.AsyncClient(timeout=AUTO_DETECT_TIMEOUT) as client:
        for endpoint in key_added:
            if _another_server_is_chosen(store, endpoint):
                continue
            answers = await _ollama_api_backend(client, _server_root(endpoint.base_url)) is not None
            if answers and store.change_kind(endpoint.id, OLLAMA_KIND):
                logger.info("the endpoint at %s was added with a key and speaks the Ollama API on this machine; "
                            "it is filed as %s", endpoint.base_url, OLLAMA_KIND)


async def filing_for_local_server(base_url: str) -> tuple[str, str] | None:
    address = base_url.strip().rstrip("/")
    if not _is_on_this_machine(address):
        return None
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        kind = await _api_spoken_by(client, address)
    if kind == OLLAMA_KIND:
        return address, OLLAMA_KIND
    if kind == OPENAI_COMPATIBLE_KIND:
        return f"{_server_root(address)}{OPENAI_PATH_SUFFIX}", OPENAI_COMPATIBLE_KIND
    return None


def ollama_serve_would_answer_at(address: str | None) -> bool:
    if not address:
        return True
    try:
        parsed = urlsplit(address)
        return parsed.hostname in THIS_MACHINE_HOSTS and parsed.port == OLLAMA_PORT
    except ValueError:
        return False


async def settle_local_backend(store: ModelEndpointStore, env_override: str | None,
                               start_ollama: Callable[[str | None], Awaitable[bool]]) -> ModelEndpoint | None:
    if env_override:
        return await add_detected_backend(store, env_override)
    await _file_local_servers_by_the_api_they_speak(store)
    await _file_key_added_ollama_on_this_machine_that_answers(store)
    stored = stored_local_backend(store)
    if stored is not None:
        await _confirm_stored_backend(store, stored, start_ollama)
        return None
    set_up = _set_up_ollama_api_servers(store)
    if set_up:
        logger.info("not looking for a model server on this machine: the Ollama-API server at %s is set up",
                    set_up[0].base_url)
        return None
    return await _adopt_first_local_backend(store, start_ollama)


async def answering_ollama_address(env_override: str | None) -> str | None:
    candidates = [env_override] if env_override else [f"http://{host}:{OLLAMA_PORT}" for host in LOCAL_HOSTS]
    async with httpx.AsyncClient(timeout=AUTO_DETECT_TIMEOUT) as client:
        for address in candidates:
            if await _ollama_api_backend(client, address) is not None:
                return address
    return None


async def adopt_started_ollama(store: ModelEndpointStore, env_override: str | None) -> ModelEndpoint | None:
    if chosen_backend_url(store, env_override):
        return None
    address = await answering_ollama_address(None)
    return await add_detected_backend(store, address) if address else None
