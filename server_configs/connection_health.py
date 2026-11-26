from __future__ import annotations

import asyncio
import functools
import json
import logging
import math
import os
import time
from dataclasses import dataclass, field

import httpx

from atomic_io import atomic_write_json
from providers import build_headers, build_models_url, is_embedding_model

logger = logging.getLogger(__name__)

CHECK_EVERY_SECONDS = 60.0
CHECK_TIMEOUT_SECONDS = 5.0
CHECK_DEADLINE_SECONDS = 5.0
MAX_LIST_BYTES = 1024 * 1024
MAX_LISTED_MODELS = 1000
MAX_MODEL_ID_CHARS = 200
REMEMBERED_FILE = "connection_models.json"
REMEMBER_AGAIN_AFTER_SECONDS = 3600.0
REMEMBERED_MODEL_FIELDS = ("id", "endpoint_id", "provider", "size")
OLLAMA_PROVIDER = "ollama"
ENGINE_REMOTE_PREFIX = "remote/"
UNAUTHORIZED_STATUSES = (401, 403)
SERVER_ERROR_FROM = 500
TIMEOUT = "timeout"
UNREACHABLE = "unreachable"
UNAUTHORIZED = "unauthorized"
SERVER_ERROR = "server_error"
HTTP_ERROR = "http_error"
BAD_ANSWER = "bad_answer"
IDENTITY_ENCODING = "identity"
FAILURE_WORDS = {TIMEOUT: "timed out", UNREACHABLE: "unreachable", UNAUTHORIZED: "key refused",
                 SERVER_ERROR: "server error", HTTP_ERROR: "error answer", BAD_ANSWER: "unreadable answer"}


class CheckFailed(Exception):

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


@dataclass
class ConnectionState:
    available: bool
    last_checked: float
    last_change: float
    error_kind: str = ""
    models: list[dict] = field(default_factory=list)

    def why_down(self) -> str:
        return FAILURE_WORDS.get(self.error_kind, "not answering")

    def public(self) -> dict:
        return {"available": self.available, "last_checked": self.last_checked, "last_change": self.last_change,
                "error_kind": self.error_kind}


def _is_model_entry(entry, id_field: str) -> bool:
    return (isinstance(entry, dict) and isinstance(entry.get(id_field), str)
            and 0 < len(entry[id_field]) <= MAX_MODEL_ID_CHARS)


def _listed_entries(endpoint, data, list_field: str, id_field: str) -> list[dict]:
    entries = data.get(list_field) if isinstance(data, dict) else None
    if not isinstance(entries, list) or len(entries) > MAX_LISTED_MODELS:
        raise CheckFailed(BAD_ANSWER)
    readable = [entry for entry in entries if _is_model_entry(entry, id_field)]
    if entries and not readable:
        raise CheckFailed(BAD_ANSWER)
    if len(readable) != len(entries):
        logger.warning("model list of connection %s: skipped %d unreadable entries", endpoint.id,
                       len(entries) - len(readable))
    return readable


def models_in_answer(endpoint, data) -> list[dict]:
    if endpoint.provider == OLLAMA_PROVIDER:
        return [{"id": m["name"], "endpoint_id": endpoint.id, "provider": OLLAMA_PROVIDER, "size": m.get("size")}
                for m in _listed_entries(endpoint, data, "models", "name")
                if not is_embedding_model(m["name"]) and not m["name"].startswith(ENGINE_REMOTE_PREFIX)]
    return [{"id": m["id"], "endpoint_id": endpoint.id, "provider": endpoint.provider}
            for m in _listed_entries(endpoint, data, "data", "id")]


def failure_kind(status: int) -> str:
    if status in UNAUTHORIZED_STATUSES:
        return UNAUTHORIZED
    return SERVER_ERROR if status >= SERVER_ERROR_FROM else HTTP_ERROR


def deadline_seconds() -> float:
    return CHECK_DEADLINE_SECONDS


async def _capped_body(answer: httpx.Response) -> bytes:
    body = bytearray()
    async for chunk in answer.aiter_bytes():
        body += chunk
        if len(body) > MAX_LIST_BYTES or answer.num_bytes_downloaded > MAX_LIST_BYTES:
            raise CheckFailed(BAD_ANSWER)
    return bytes(body)


def list_headers(api_key: str | None, base_url: str) -> dict:
    return {**build_headers(api_key, base_url), "Accept-Encoding": IDENTITY_ENCODING}


async def fetch_models(client: httpx.AsyncClient, endpoint, api_key: str | None) -> list[dict]:
    url, headers = build_models_url(endpoint.base_url), list_headers(api_key, endpoint.base_url)
    try:
        async with client.stream("GET", url, headers=headers) as answer:
            if not answer.is_success:
                raise CheckFailed(failure_kind(answer.status_code))
            body = await _capped_body(answer)
    except httpx.TimeoutException as error:
        raise CheckFailed(TIMEOUT) from error
    except httpx.HTTPError as error:
        raise CheckFailed(UNREACHABLE) from error
    try:
        return models_in_answer(endpoint, json.loads(body))
    except (ValueError, RecursionError) as error:
        raise CheckFailed(BAD_ANSWER) from error


async def models_or_failure(endpoint, api_key_fn) -> tuple[list[dict] | None, str]:
    try:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
            models = await asyncio.wait_for(fetch_models(client, endpoint, api_key_fn(endpoint)), deadline_seconds())
    except CheckFailed as failure:
        return None, failure.kind
    except asyncio.TimeoutError:
        return None, TIMEOUT
    except Exception as error:
        logger.warning("checking model connection %s failed: %s", endpoint.id, type(error).__name__)
        return None, BAD_ANSWER
    return models, ""


def _finite_number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _remembered_model(entry, endpoint_id: str) -> dict | None:
    if not _is_model_entry(entry, "id") or entry.get("endpoint_id") != endpoint_id:
        return None
    if "provider" in entry and not isinstance(entry["provider"], str):
        return None
    if entry.get("size") is not None and _finite_number(entry["size"]) is None:
        return None
    return {name: entry[name] for name in REMEMBERED_MODEL_FIELDS if name in entry}


def _remembered_entry(raw, endpoint_id: str) -> dict | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("models"), list):
        return None
    saved_at = _finite_number(raw.get("saved_at"))
    if saved_at is None:
        return None
    models = [_remembered_model(entry, endpoint_id) for entry in raw["models"][:MAX_LISTED_MODELS]]
    return {"models": [model for model in models if model], "saved_at": saved_at}


class ConnectionHealth:

    def __init__(self, endpoints_fn, api_key_fn, clock=time.time, data_dir: str | None = None):
        self.endpoints_fn = endpoints_fn
        self.api_key_fn = api_key_fn
        self.clock = clock
        self._remembered_path = os.path.join(data_dir, REMEMBERED_FILE) if data_dir else None
        self._remembered = self._load_remembered()
        self._states: dict[str, ConnectionState] = {}
        self._running: dict[str, asyncio.Future] = {}
        self._stop = asyncio.Event()

    def _load_remembered(self) -> dict[str, dict]:
        if not self._remembered_path or not os.path.exists(self._remembered_path):
            return {}
        try:
            return self._read_remembered()
        except Exception as error:
            logger.warning("ignoring the unreadable last known model lists in %s: %s", self._remembered_path,
                           type(error).__name__)
            return {}

    def _read_remembered(self) -> dict[str, dict]:
        with open(self._remembered_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        known = {endpoint.id for endpoint in self.endpoints_fn()}
        entries = {endpoint_id: _remembered_entry(entry, endpoint_id) for endpoint_id, entry in raw.items()} if isinstance(raw, dict) else {}
        kept = {endpoint_id: entry for endpoint_id, entry in entries.items() if entry and endpoint_id in known}
        if len(kept) != len(entries):
            self._remembered = kept
            self._save_remembered()
        return kept

    def _save_remembered(self) -> None:
        if not self._remembered_path:
            return
        try:
            atomic_write_json(self._remembered_path, self._remembered)
        except OSError as error:
            logger.warning("could not save the last known model lists: %s", type(error).__name__)

    def _remember(self, endpoint_id: str, models: list[dict], now: float) -> None:
        before = self._remembered.get(endpoint_id)
        entry = {"models": [m for m in (_remembered_model(model, endpoint_id) for model in models) if m], "saved_at": now}
        unchanged = before is not None and before["models"] == entry["models"]
        if unchanged and now - before["saved_at"] < REMEMBER_AGAIN_AFTER_SECONDS:
            return
        self._remembered[endpoint_id] = entry
        self._save_remembered()

    def remembered_state(self, endpoint_id: str) -> ConnectionState | None:
        entry = self._remembered.get(endpoint_id)
        if entry is None:
            return None
        return ConnectionState(available=False, last_checked=entry["saved_at"], last_change=entry["saved_at"],
                               models=list(entry["models"]))

    def state(self, endpoint_id: str) -> ConnectionState | None:
        return self._states.get(endpoint_id)

    def _record(self, endpoint, available: bool, error_kind: str, models: list[dict] | None) -> None:
        now = self.clock()
        before = self._states.get(endpoint.id)
        changed = before is None or before.available != available
        if models is not None:
            self._remember(endpoint.id, models, now)
        remembered = self._remembered.get(endpoint.id)
        kept_models = models if models is not None else (before.models if before else (remembered or {}).get("models", []))
        self._states[endpoint.id] = ConnectionState(available=available, last_checked=now,
                                                    last_change=now if changed else before.last_change,
                                                    error_kind=error_kind, models=kept_models)
        if changed and before is not None:
            logger.info("model connection %s is now %s%s", endpoint.label or endpoint.base_url,
                        "available" if available else "unavailable", f" ({error_kind})" if error_kind else "")

    async def _check_now(self, endpoint) -> None:
        models, failure = await models_or_failure(endpoint, self.api_key_fn)
        self._record(endpoint, not failure, failure, models)

    async def check(self, endpoint) -> None:
        running = self._running.get(endpoint.id)
        if running is None or running.get_loop() is not asyncio.get_running_loop():
            running = asyncio.ensure_future(self._check_now(endpoint))
            self._running[endpoint.id] = running
            running.add_done_callback(functools.partial(self._forget_finished, endpoint.id))
        await asyncio.shield(running)

    def _forget_finished(self, endpoint_id: str, done: asyncio.Future) -> None:
        if self._running.get(endpoint_id) is done:
            del self._running[endpoint_id]

    def _forget_removed(self, endpoints) -> None:
        known = {endpoint.id for endpoint in endpoints}
        for endpoint_id in [endpoint_id for endpoint_id in self._states if endpoint_id not in known]:
            del self._states[endpoint_id]
        gone = [endpoint_id for endpoint_id in self._remembered if endpoint_id not in known]
        for endpoint_id in gone:
            del self._remembered[endpoint_id]
        if gone:
            self._save_remembered()

    async def check_all(self) -> None:
        endpoints = self.endpoints_fn()
        self._forget_removed(endpoints)
        await asyncio.gather(*(self.check(endpoint) for endpoint in endpoints), return_exceptions=True)

    async def check_unchecked(self, endpoint_ids) -> None:
        wanted = set(endpoint_ids)
        unchecked = [e for e in self.endpoints_fn() if e.id in wanted and e.id not in self._states]
        await asyncio.gather(*(self.check(endpoint) for endpoint in unchecked), return_exceptions=True)

    async def run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.check_all()
            except Exception:
                logger.exception("checking the model connections failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=CHECK_EVERY_SECONDS)
            except asyncio.TimeoutError:
                logger.debug("model connection check woke after %ss", CHECK_EVERY_SECONDS)

    def stop(self) -> None:
        self._stop.set()
