from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
from dataclasses import dataclass, field

import httpx

from providers import build_headers, build_models_url, is_embedding_model

logger = logging.getLogger(__name__)

CHECK_EVERY_SECONDS = 60.0
CHECK_TIMEOUT_SECONDS = 5.0
CHECK_DEADLINE_SECONDS = 5.0
MAX_LIST_BYTES = 1024 * 1024
MAX_LISTED_MODELS = 1000
MAX_MODEL_ID_CHARS = 200
OLLAMA_PROVIDER = "ollama"
UNAUTHORIZED_STATUSES = (401, 403)
SERVER_ERROR_FROM = 500
TIMEOUT = "timeout"
UNREACHABLE = "unreachable"
UNAUTHORIZED = "unauthorized"
SERVER_ERROR = "server_error"
HTTP_ERROR = "http_error"
BAD_ANSWER = "bad_answer"


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

    def public(self) -> dict:
        return {"available": self.available, "last_checked": self.last_checked, "last_change": self.last_change,
                "error_kind": self.error_kind}


def _is_model_entry(entry, id_field: str) -> bool:
    return (isinstance(entry, dict) and isinstance(entry.get(id_field), str)
            and 0 < len(entry[id_field]) <= MAX_MODEL_ID_CHARS)


def _listed_entries(data, list_field: str, id_field: str) -> list[dict]:
    entries = data.get(list_field) if isinstance(data, dict) else None
    if not isinstance(entries, list) or len(entries) > MAX_LISTED_MODELS:
        raise CheckFailed(BAD_ANSWER)
    if not all(_is_model_entry(entry, id_field) for entry in entries):
        raise CheckFailed(BAD_ANSWER)
    return entries


def models_in_answer(endpoint, data) -> list[dict]:
    if endpoint.provider == OLLAMA_PROVIDER:
        return [{"id": m["name"], "endpoint_id": endpoint.id, "provider": OLLAMA_PROVIDER, "size": m.get("size")}
                for m in _listed_entries(data, "models", "name") if not is_embedding_model(m["name"])]
    return [{"id": m["id"], "endpoint_id": endpoint.id, "provider": endpoint.provider}
            for m in _listed_entries(data, "data", "id")]


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
        if len(body) > MAX_LIST_BYTES:
            raise CheckFailed(BAD_ANSWER)
    return bytes(body)


async def fetch_models(client: httpx.AsyncClient, endpoint, api_key: str | None) -> list[dict]:
    url, headers = build_models_url(endpoint.base_url), build_headers(api_key, endpoint.base_url)
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


class ConnectionHealth:

    def __init__(self, endpoints_fn, api_key_fn, clock=time.time):
        self.endpoints_fn = endpoints_fn
        self.api_key_fn = api_key_fn
        self.clock = clock
        self._states: dict[str, ConnectionState] = {}
        self._running: dict[str, asyncio.Future] = {}
        self._stop = asyncio.Event()

    def state(self, endpoint_id: str) -> ConnectionState | None:
        return self._states.get(endpoint_id)

    def _record(self, endpoint, available: bool, error_kind: str, models: list[dict] | None) -> None:
        now = self.clock()
        before = self._states.get(endpoint.id)
        changed = before is None or before.available != available
        kept_models = models if models is not None else (before.models if before else [])
        self._states[endpoint.id] = ConnectionState(available=available, last_checked=now,
                                                    last_change=now if changed else before.last_change,
                                                    error_kind=error_kind, models=kept_models)
        if changed and before is not None:
            logger.info("model connection %s is now %s%s", endpoint.label or endpoint.base_url,
                        "available" if available else "unavailable", f" ({error_kind})" if error_kind else "")

    async def _check_now(self, endpoint) -> None:
        try:
            async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
                models = await asyncio.wait_for(fetch_models(client, endpoint, self.api_key_fn(endpoint)),
                                                deadline_seconds())
        except CheckFailed as failure:
            self._record(endpoint, False, failure.kind, None)
            return
        except asyncio.TimeoutError:
            self._record(endpoint, False, TIMEOUT, None)
            return
        except Exception as error:
            logger.warning("checking model connection %s failed: %s", endpoint.id, type(error).__name__)
            self._record(endpoint, False, BAD_ANSWER, None)
            return
        self._record(endpoint, True, "", models)

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
