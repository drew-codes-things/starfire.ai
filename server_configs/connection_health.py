from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

from providers import build_headers, build_models_url, is_embedding_model

logger = logging.getLogger(__name__)

CHECK_EVERY_SECONDS = 60.0
CHECK_TIMEOUT_SECONDS = 5.0
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


def models_in_answer(endpoint, data) -> list[dict]:
    if not isinstance(data, dict):
        raise CheckFailed(BAD_ANSWER)
    if endpoint.provider == OLLAMA_PROVIDER:
        return [{"id": m.get("name"), "endpoint_id": endpoint.id, "provider": OLLAMA_PROVIDER, "size": m.get("size")}
                for m in data.get("models", []) if isinstance(m, dict) and not is_embedding_model(m.get("name"))]
    return [{"id": m.get("id"), "endpoint_id": endpoint.id, "provider": endpoint.provider}
            for m in data.get("data", []) if isinstance(m, dict)]


def failure_kind(status: int) -> str:
    if status in UNAUTHORIZED_STATUSES:
        return UNAUTHORIZED
    return SERVER_ERROR if status >= SERVER_ERROR_FROM else HTTP_ERROR


async def fetch_models(client: httpx.AsyncClient, endpoint, api_key: str | None) -> list[dict]:
    try:
        answer = await client.get(build_models_url(endpoint.base_url), headers=build_headers(api_key, endpoint.base_url))
    except httpx.TimeoutException as error:
        raise CheckFailed(TIMEOUT) from error
    except httpx.HTTPError as error:
        raise CheckFailed(UNREACHABLE) from error
    if answer.is_error:
        raise CheckFailed(failure_kind(answer.status_code))
    try:
        return models_in_answer(endpoint, answer.json())
    except ValueError as error:
        raise CheckFailed(BAD_ANSWER) from error


class ConnectionHealth:

    def __init__(self, endpoints_fn, api_key_fn, clock=time.time):
        self.endpoints_fn = endpoints_fn
        self.api_key_fn = api_key_fn
        self.clock = clock
        self._states: dict[str, ConnectionState] = {}
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

    async def check(self, client: httpx.AsyncClient, endpoint) -> None:
        try:
            models = await fetch_models(client, endpoint, self.api_key_fn(endpoint))
        except CheckFailed as failure:
            self._record(endpoint, False, failure.kind, None)
            return
        self._record(endpoint, True, "", models)

    async def check_all(self, only_unchecked: bool = False) -> None:
        endpoints = self.endpoints_fn()
        known = {endpoint.id for endpoint in endpoints}
        self._states = {endpoint_id: state for endpoint_id, state in self._states.items() if endpoint_id in known}
        wanted = [e for e in endpoints if not (only_unchecked and e.id in self._states)]
        if not wanted:
            return
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
            await asyncio.gather(*(self.check(client, endpoint) for endpoint in wanted))

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
