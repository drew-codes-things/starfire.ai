from __future__ import annotations

import asyncio
import functools
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StrictBool, StrictInt

import auth_routes
import caller_watch
import screen_checks
import content_screen
import remote_rules
import routes
import user_data
from app_keys import (EVERY_MODEL, MAX_ENDPOINT_ID_CHARS, MAX_KEY_NAME_CHARS, MAX_MODEL_NAME_CHARS, AppKey,
                      AppKeyError, key_grants)
from auth import LOCKOUT_SECONDS, LoginLimiter, client_address
from chat_stream import (CONNECT_PHASE_ERRORS, CUT_OFF_KIND, REPLY_TOO_LONG, BrokenOffAfterSending, NeverReachedServer,
                         stream_chat_once)
from config import config
from connection_health import CHECK_EVERY_SECONDS
from generation_quota import GenerationQuota
from providers import build_chat_url, build_headers
from reply_runs import MAX_REPLY_BYTES, MAX_RUNNING_PER_ACCOUNT

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/api/app-keys")
openai_router = APIRouter(prefix="/v1")
BAD_KEY_MAX_FAILURES = 10
bad_key_limiter = LoginLimiter(max_failures=BAD_KEY_MAX_FAILURES, lockout_seconds=LOCKOUT_SECONDS)
key_minutes = GenerationQuota()
upstream_transport: httpx.AsyncBaseTransport | None = None

UPSTREAM_TIMEOUT = httpx.Timeout(600.0, connect=10.0)
BEARER = "bearer"
MAX_MESSAGES = 4096
LENGTH_FINISH = "length"
MAX_JSON_DEPTH = 64
MAX_JSON_VALUES = 200_000
MAX_RUNNING_PER_KEY = MAX_RUNNING_PER_ACCOUNT
KEY_RECHECK_SECONDS = 1.0
REPLY_DEADLINE_SECONDS = 1200.0
GIVE_BACK_WITHIN_SECONDS = 5.0
IDENTITY_ENCODING = "identity"
UPSTREAM_KEY_REFUSED = {401, 403}
PASSED_ON_STATUSES = {400, 404, 409, 413, 422, 429}
OPENAI_PROVIDER = "openai"
ANTHROPIC_PROVIDER = "anthropic"
ADMIN_ROLE = "admin"
MAX_USER_ID_CHARS = 64
ID_SEPARATOR = "/"
OPTION_NAMES = {"temperature": "temperature", "top_p": "top_p", "top_k": "top_k", "seed": "seed",
                "max_tokens": "num_predict", "max_completion_tokens": "num_predict"}
OPENAI_FIELDS = frozenset({"model", "messages", "stream", "temperature", "top_p", "max_tokens", "tools", "tool_choice",
                           "stop"})
OPENAI_MESSAGE_FIELDS = frozenset({"role", "content", "name", "tool_calls", "tool_call_id"})
NOT_VALID = "this app key is not valid: it is wrong, revoked or expired, or its account is closed"
NO_KEY = "send an app key as 'Authorization: Bearer sf_...'"
STOPPED = "the app key was revoked or its account was closed, so the reply was stopped"
UPSTREAM_FAILED = "the model server could not answer this request"
STARFIRE_FAILED = "starfire could not answer this request"
TOO_LONG = "the model server's reply was too long, so starfire stopped it"
TOO_SLOW = "the model server took too long, so starfire stopped the reply"
CALLER_GONE = "the caller went away before the reply was ready"
CALLER_GONE_STATUS = 499
NOT_UNICODE = "the request holds text that is not valid Unicode"
RULES_MISSING = "Arty's rules for this model couldn't be loaded, so it can't answer right now; try again in a minute"
SYSTEM_ROLES = frozenset({"system", "developer"})
TEXT_PART = "text"


class ApiRefusal(Exception):

    def __init__(self, status: int, message: str, code: str, headers: dict | None = None,
                 gives_back_reply: bool = False):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.headers = headers or {}
        self.gives_back_reply = gives_back_reply


@dataclass
class ReplySlot:
    running: dict[str, int]
    key_id: str
    released: bool = False

    def release(self) -> None:
        if self.released:
            return
        self.released = True
        left = self.running.get(self.key_id, 0) - 1
        if left > 0:
            self.running[self.key_id] = left
        else:
            self.running.pop(self.key_id, None)


@dataclass
class RunningReplies:
    limit: int
    running: dict[str, int] = field(default_factory=dict)

    def take(self, key_id: str) -> ReplySlot:
        if self.running.get(key_id, 0) >= self.limit:
            raise ApiRefusal(429, f"this app key already has {self.limit} replies running; wait for one to finish",
                             "rate_limit_exceeded", {"Retry-After": "1"})
        self.running[key_id] = self.running.get(key_id, 0) + 1
        return ReplySlot(self.running, key_id)


running_replies = RunningReplies(MAX_RUNNING_PER_KEY)


def error_body(message: str, code: str) -> dict:
    return {"error": {"message": message, "type": code, "code": code}}


def refusal_response(refusal: ApiRefusal) -> Response:
    return Response(json.dumps(error_body(refusal.message, refusal.code)), status_code=refusal.status,
                    headers=refusal.headers, media_type="application/json")


def bad_request(message: str) -> ApiRefusal:
    return ApiRefusal(400, message, "invalid_request_error")


def presented_key(request: Request) -> str:
    scheme, _, value = request.headers.get("authorization", "").strip().partition(" ")
    return value.strip() if scheme.lower() == BEARER else ""


def open_account(user_id: str):
    account = auth_routes.users.find_by_id(user_id)
    return account if account and not account.disabled else None


def is_admin_account(user_id: str) -> bool:
    account = open_account(user_id)
    return account is not None and account.role == ADMIN_ROLE


def caller_address(request: Request) -> str:
    return client_address(request, auth_routes.TRUSTED_PROXIES, config.client_ip_header)


def refuse_wrong_key(request: Request) -> ApiRefusal:
    address = caller_address(request)
    wait = bad_key_limiter.seconds_locked(address)
    if wait:
        return ApiRefusal(429, f"too many wrong app keys from this address - try again in {wait} s",
                          "rate_limit_exceeded", {"Retry-After": str(wait)})
    bad_key_limiter.record_failure(address)
    logger.warning("refused a wrong app key on %s from %s", request.url.path, address)
    return ApiRefusal(401, NOT_VALID, "invalid_api_key")


def take_a_minute_slot(key: AppKey) -> None:
    refusal = key_minutes.take(key.id, config.app_key_requests_per_minute, 0)
    if refusal:
        raise ApiRefusal(429, f"this app key may make {config.app_key_requests_per_minute} requests a minute; try again "
                         f"in {refusal.retry_after_seconds} s", "rate_limit_exceeded",
                         {"Retry-After": str(refusal.retry_after_seconds)})


def authenticate(request: Request) -> AppKey:
    secret = presented_key(request)
    if not secret:
        raise ApiRefusal(401, NO_KEY, "invalid_api_key")
    key = auth_routes.app_keys.find(secret)
    if key is None or open_account(key.user_id) is None:
        raise refuse_wrong_key(request)
    take_a_minute_slot(key)
    return key


def public_id(endpoint_id: str, model: str) -> str:
    return f"{endpoint_id}{ID_SEPARATOR}{model}"


def split_id(model_id: str) -> tuple[str, str]:
    endpoint_id, separator, model = model_id.partition(ID_SEPARATOR)
    if not separator or not endpoint_id or not model:
        raise ApiRefusal(404, f"{model_id} is not a model id from GET /v1/models (they look like <connection>/<model>)",
                         "model_not_found")
    return endpoint_id, model


def brings_own_tools(model: str) -> bool:
    return routes._brings_own_tools(model)


def model_names(state) -> list[str]:
    return list(dict.fromkeys(m["id"] for m in state.models if isinstance(m.get("id"), str))) if state else []


def server_address(url: str) -> str:
    address = url.strip().rstrip("/").lower()
    return address.removesuffix("/v1").rstrip("/")


def is_arty_address(endpoint) -> bool:
    arty = server_address(config.arty_base_url)
    return bool(arty) and server_address(endpoint.base_url) == arty


def is_own_tools_connection(endpoint, state) -> bool:
    return is_arty_address(endpoint) or any(brings_own_tools(name) for name in model_names(state))


def needs_arty_option(endpoint, state, model: str) -> bool:
    return brings_own_tools(model) or is_own_tools_connection(endpoint, state)


def may_offer(key: AppKey, endpoint, state, model: str) -> bool:
    if not key_grants(key, endpoint.id, model):
        return False
    if needs_arty_option(endpoint, state, model) and not (key.allow_own_tools and is_admin_account(key.user_id)):
        return False
    with user_data.run_as(key.user_id):
        return routes._may_use_model(model)


async def checked_connections(endpoint_ids=None) -> list[tuple]:
    endpoints = [e for e in routes.endpoints.list() if endpoint_ids is None or e.id in endpoint_ids]
    await routes.connection_health.check_unchecked([endpoint.id for endpoint in endpoints])
    checked = []
    for endpoint in endpoints:
        state = routes.connection_health.state(endpoint.id)
        if state is not None:
            checked.append((endpoint, state))
    return checked


def granted_connections(key: AppKey) -> set[str]:
    return {grant["endpoint_id"] for grant in key.grants}


def log_outcome(key: AppKey, path: str, model: str, status: int) -> None:
    logger.info("app key %r: %s model %r answered %d", key.name, path, model, status)


@openai_router.get("/models")
async def list_models(request: Request):
    try:
        key = authenticate(request)
    except ApiRefusal as refusal:
        return refusal_response(refusal)
    offered = [{"id": public_id(endpoint.id, name), "object": "model", "created": 0, "owned_by": "starfire",
                "available": state.available}
               for endpoint, state in await checked_connections(granted_connections(key)) for name in model_names(state)
               if may_offer(key, endpoint, state, name) and not routes.is_hidden_model(name)]
    log_outcome(key, request.url.path, "", 200)
    return {"object": "list", "data": offered}


def is_unicode(text: str) -> bool:
    if text.isascii():
        return True
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def json_shape_problem(value, depth_limit: int, value_budget: int) -> str:
    waiting, seen = [(value, 1)], 0
    while waiting:
        item, depth = waiting.pop()
        seen += 1
        if depth > depth_limit:
            return f"the request is nested more than {depth_limit} levels deep"
        if isinstance(item, str) and not is_unicode(item):
            return NOT_UNICODE
        children = list(item.values()) if isinstance(item, dict) else item if isinstance(item, list) else []
        if seen + len(waiting) + len(children) > value_budget:
            return f"the request holds more than {value_budget} JSON values"
        if isinstance(item, dict) and not all(is_unicode(name) for name in item):
            return NOT_UNICODE
        waiting.extend((child, depth + 1) for child in children)
    return ""


def is_tool_call(call) -> bool:
    function = call.get("function") if isinstance(call, dict) else None
    return (isinstance(function, dict) and isinstance(function.get("name"), str)
            and isinstance(function.get("arguments", ""), (str, dict)))


def is_tool(tool) -> bool:
    function = tool.get("function") if isinstance(tool, dict) else None
    return isinstance(function, dict) and isinstance(function.get("name"), str) and bool(function["name"])


def check_message(index: int, message) -> None:
    if not isinstance(message, dict) or not isinstance(message.get("role"), str):
        raise bad_request(f"message {index} must be a JSON object with a role")
    if not isinstance(message.get("content"), (str, list, type(None))):
        raise bad_request(f"message {index}: content must be text, a list of parts or null")
    calls = message.get("tool_calls")
    if calls is not None and not (isinstance(calls, list) and all(is_tool_call(call) for call in calls)):
        raise bad_request(f"message {index}: tool_calls must be a list of {{function: {{name, arguments}}}} objects")


def refuse_json_constant(name: str):
    raise ValueError(f"{name} is not a JSON number")


def finite_number(text: str) -> float:
    number = float(text)
    if not math.isfinite(number):
        raise ValueError(f"{text} is too large to be a number starfire can pass on")
    return number


def parsed_json(raw: bytes):
    try:
        return json.loads(raw, parse_constant=refuse_json_constant, parse_float=finite_number)
    except (ValueError, RecursionError) as error:
        raise bad_request("the request body must be JSON, without NaN, Infinity or numbers too big to hold") from error


def check_request_shape(body) -> None:
    if not isinstance(body, dict):
        raise bad_request("the request body must be a JSON object")
    model, messages, tools = body.get("model"), body.get("messages"), body.get("tools")
    if not isinstance(model, str) or not 0 < len(model) <= MAX_ENDPOINT_ID_CHARS + MAX_MODEL_NAME_CHARS + 1:
        raise bad_request("model must be a model id from GET /v1/models")
    if not isinstance(messages, list) or not 0 < len(messages) <= MAX_MESSAGES:
        raise bad_request(f"messages must be a list of 1 to {MAX_MESSAGES} messages")
    for index, message in enumerate(messages):
        check_message(index, message)
    if tools is not None and not (isinstance(tools, list) and all(is_tool(tool) for tool in tools)):
        raise bad_request("tools must be a list of {type: \"function\", function: {name, ...}} objects")
    if not isinstance(body.get("stream", False), bool):
        raise bad_request("stream must be true or false")
    if body.get("n", 1) != 1:
        raise bad_request("starfire gives one reply per request: leave n out or set it to 1")


def checked_request(raw: bytes) -> dict:
    body = parsed_json(raw)
    check_request_shape(body)
    problem = json_shape_problem(body, MAX_JSON_DEPTH, MAX_JSON_VALUES)
    if problem:
        raise bad_request(problem)
    return body


async def read_request(request: Request) -> dict:
    return await asyncio.to_thread(checked_request, await request.body())


def take_replies(key: AppKey) -> None:
    try:
        reached = auth_routes.app_keys.take_reply(key.id)
    except AppKeyError as error:
        raise ApiRefusal(401, NOT_VALID, "invalid_api_key") from error
    if reached:
        raise ApiRefusal(429, f"this app key has used its {reached.limit} replies for today; the count starts again "
                         "at midnight UTC", "rate_limit_exceeded", {"Retry-After": str(reached.retry_after_seconds)})
    with user_data.run_as(key.user_id):
        refusal = routes._generation_refusal()
    if refusal:
        auth_routes.app_keys.give_back_reply(key.id)
        raise ApiRefusal(429, refusal.message, "rate_limit_exceeded", {"Retry-After": str(refusal.retry_after_seconds)})


async def owning_connection(key: AppKey, model_id: str):
    endpoint_id, model = split_id(model_id)
    endpoint = routes.endpoints.get(endpoint_id)
    if endpoint is None:
        raise ApiRefusal(404, f"no connection offers {model_id}; GET /v1/models lists what this key may use",
                         "model_not_found")
    if not key_grants(key, endpoint_id, model):
        raise ApiRefusal(403, f"this app key may not use {model_id}", "model_not_allowed")
    await routes.connection_health.check_unchecked([endpoint_id])
    state = routes.connection_health.state(endpoint_id)
    if not may_offer(key, endpoint, state, model):
        raise ApiRefusal(403, f"this app key may not use {model_id}", "model_not_allowed")
    if state is None or not state.available:
        raise ApiRefusal(503, f"the model server for {model_id} is not answering right now; try again later",
                         "model_unavailable", {"Retry-After": str(int(CHECK_EVERY_SECONDS))})
    if model not in model_names(state):
        raise ApiRefusal(404, f"the connection does not offer {model}; GET /v1/models lists what this key may use",
                         "model_not_found")
    return endpoint, model, needs_arty_option(endpoint, state, model)


def key_still_works(key: AppKey) -> bool:
    return auth_routes.app_keys.is_live(key.id) and open_account(key.user_id) is not None


class ReplyWatch:

    def __init__(self, key: AppKey):
        self.key = key
        self.started = self.checked_at = time.monotonic()

    def stop_notice(self) -> bytes | None:
        now = time.monotonic()
        if now - self.started >= REPLY_DEADLINE_SECONDS:
            return sse(error_body(TOO_SLOW, "upstream_error"))
        if now - self.checked_at < KEY_RECHECK_SECONDS:
            return None
        self.checked_at = now
        return None if key_still_works(self.key) else sse(error_body(STOPPED, "invalid_api_key"))

    def seconds_to_next_check(self) -> float:
        next_check = min(self.checked_at + KEY_RECHECK_SECONDS, self.started + REPLY_DEADLINE_SECONDS)
        return max(0.0, next_check - time.monotonic())


@dataclass
class FeedEnd:
    error: BaseException | None = None


async def feed(chunks, queue: asyncio.Queue) -> None:
    try:
        async for chunk in chunks:
            await queue.put(chunk)
        await queue.put(FeedEnd())
    except Exception as error:
        await queue.put(FeedEnd(error))
    finally:
        await chunks.aclose()


async def next_or_stop(queue: asyncio.Queue, watch: ReplyWatch) -> tuple[object, bool]:
    getting = asyncio.ensure_future(queue.get())
    try:
        while True:
            notice = watch.stop_notice()
            if notice:
                return notice, True
            await asyncio.wait({getting}, timeout=watch.seconds_to_next_check())
            if getting.done():
                return getting.result(), False
    finally:
        await stop_task(getting)


async def while_the_key_works(chunks, key: AppKey):
    watch, queue = ReplyWatch(key), asyncio.Queue(maxsize=1)
    feeder = asyncio.ensure_future(feed(chunks, queue))
    try:
        while True:
            item, stopped = await next_or_stop(queue, watch)
            if isinstance(item, FeedEnd):
                if item.error is not None:
                    raise item.error
                return
            yield item
            if stopped:
                return
    finally:
        await stop_task(feeder)


async def stop_task(task: asyncio.Future) -> None:
    if not task.done():
        task.cancel()
    await asyncio.wait({task})


async def unless_stopped(work, request: Request, key: AppKey):
    task = asyncio.ensure_future(work)
    hung_up = asyncio.ensure_future(caller_watch.caller_hangs_up(request))
    deadline = time.monotonic() + REPLY_DEADLINE_SECONDS
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise ApiRefusal(504, TOO_SLOW, "upstream_error")
            await asyncio.wait({task, hung_up}, timeout=min(KEY_RECHECK_SECONDS, left),
                               return_when=asyncio.FIRST_COMPLETED)
            if task.done():
                return task.result()
            if hung_up.done():
                hung_up.result()
                raise ApiRefusal(CALLER_GONE_STATUS, CALLER_GONE, "client_closed")
            if not key_still_works(key):
                raise ApiRefusal(401, STOPPED, "invalid_api_key")
    finally:
        await stop_task(hung_up)
        await stop_task(task)


STREAM_DONE = b"data: [DONE]\n\n"


def sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


def json_response(payload: dict) -> Response:
    return Response(json.dumps(payload), media_type="application/json")


def answered_quickly(sent_at: float) -> bool:
    return time.monotonic() - sent_at <= GIVE_BACK_WITHIN_SECONDS


def may_give_back_after(failure: str, sent_at: float) -> bool:
    if isinstance(failure, BrokenOffAfterSending):
        return False
    return isinstance(failure, NeverReachedServer) or answered_quickly(sent_at)


def upstream_refusal(status: int, quickly: bool) -> ApiRefusal:
    if status in UPSTREAM_KEY_REFUSED:
        return ApiRefusal(502, "the model server refused starfire's key for it", "upstream_error",
                          gives_back_reply=quickly)
    if status in PASSED_ON_STATUSES:
        return ApiRefusal(status, f"the model server refused this request ({status})", "upstream_refused")
    return ApiRefusal(502, UPSTREAM_FAILED, "upstream_error", gives_back_reply=quickly)


def refused_upstream(upstream: httpx.Response, key: AppKey, model: str, sent_at: float) -> ApiRefusal:
    logger.warning("app key %r: the model server for %r answered %d", key.name, model, upstream.status_code)
    return upstream_refusal(upstream.status_code, answered_quickly(sent_at))


def unreachable_upstream(error: httpx.HTTPError, key: AppKey, model: str) -> ApiRefusal:
    logger.warning("app key %r: the model server for %r did not answer (%s)", key.name, model, type(error).__name__)
    never_reached = isinstance(error, CONNECT_PHASE_ERRORS)
    return ApiRefusal(502, UPSTREAM_FAILED, "upstream_error", gives_back_reply=never_reached)


def upstream_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT, transport=upstream_transport)


def upstream_headers(api_key: str | None, base_url: str) -> dict:
    return {**build_headers(api_key, base_url), "Accept-Encoding": IDENTITY_ENCODING}


async def capped_reply(upstream: httpx.Response) -> bytes:
    content = bytearray()
    async for chunk in upstream.aiter_bytes():
        content += chunk
        if len(content) > MAX_REPLY_BYTES or upstream.num_bytes_downloaded > MAX_REPLY_BYTES:
            raise ApiRefusal(502, TOO_LONG, "upstream_error")
    return bytes(content)


async def relayed_bytes(upstream: httpx.Response, close, key: AppKey, model: str):
    try:
        async for chunk in upstream.aiter_bytes():
            yield chunk
    except httpx.HTTPError as error:
        logger.warning("app key %r: the model server for %r broke off (%s)", key.name, model, type(error).__name__)
        yield sse(error_body(UPSTREAM_FAILED, "upstream_error"))
    finally:
        await close()


def standard_fields_only(body: dict) -> dict:
    kept = {name: value for name, value in body.items() if name in OPENAI_FIELDS}
    kept["messages"] = [{name: value for name, value in message.items() if name in OPENAI_MESSAGE_FIELDS}
                        for message in body["messages"]]
    return kept


async def forward_whole(endpoint, api_key: str | None, body: dict, key: AppKey) -> Response:
    url, headers = build_chat_url(endpoint.base_url), upstream_headers(api_key, endpoint.base_url)
    async with upstream_client() as client:
        sent_at = time.monotonic()
        try:
            async with client.stream("POST", url, headers=headers, json=body) as upstream:
                if not upstream.is_success:
                    raise refused_upstream(upstream, key, body["model"], sent_at)
                content = await capped_reply(upstream)
                media_type = upstream.headers.get("content-type", "application/json")
        except httpx.HTTPError as error:
            raise unreachable_upstream(error, key, body["model"]) from error
    return Response(screen_checks.guarded_openai_whole(content, screen_checks.reply_context(body["messages"]),
                                                      screen_checks.request_of(body["messages"])), media_type=media_type)


async def opened_upstream(client: httpx.AsyncClient, sent: httpx.Request, key: AppKey, model: str) -> httpx.Response:
    try:
        return await client.send(sent, stream=True)
    except httpx.HTTPError as error:
        raise unreachable_upstream(error, key, model) from error


async def forward_stream(endpoint, api_key: str | None, body: dict, key: AppKey, request: Request,
                         cleanups: list) -> Response:
    client = upstream_client()
    sent = client.build_request("POST", build_chat_url(endpoint.base_url),
                                headers=upstream_headers(api_key, endpoint.base_url), json=body)
    sent_at = time.monotonic()
    try:
        upstream = await unless_stopped(opened_upstream(client, sent, key, body["model"]), request, key)
    except BaseException:
        await client.aclose()
        raise

    async def close() -> None:
        await upstream.aclose()
        await client.aclose()

    if not upstream.is_success:
        await close()
        raise refused_upstream(upstream, key, body["model"], sent_at)
    cleanups.append(close)
    guarded = screen_checks.guarded_openai_stream(relayed_bytes(upstream, close, key, body["model"]),
                                                 screen_checks.reply_context(body["messages"]),
                                                 screen_checks.request_of(body["messages"]), body["model"])
    return StreamingResponse(while_the_key_works(guarded, key),
                             media_type=upstream.headers.get("content-type", "text/event-stream"),
                             headers={"Cache-Control": "no-cache"})


async def forward_as_is(endpoint, api_key: str | None, body: dict, key: AppKey, request: Request,
                        cleanups: list) -> Response:
    if body.get("stream"):
        return await forward_stream(endpoint, api_key, body, key, request, cleanups)
    return await unless_stopped(forward_whole(endpoint, api_key, body, key), request, key)


def translated_options(body: dict) -> dict | None:
    options = {}
    for name, option in OPTION_NAMES.items():
        value = body.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            options[option] = value
    return options or None


def split_system(messages: list[dict], provider: str) -> tuple[list[dict], str | None]:
    if provider != ANTHROPIC_PROVIDER:
        return messages, None
    system = "\n\n".join(m.get("content") for m in messages if m.get("role") == "system" and isinstance(m.get("content"), str))
    return [m for m in messages if m.get("role") != "system"], system or None


def openai_tool_calls(calls: list[dict], start: int = 0) -> list[dict]:
    return [{"index": start + offset, "id": call.get("id") or f"call-{start + offset}", "type": "function",
             "function": {"name": call.get("name"), "arguments": json.dumps(call.get("arguments") or {})}}
            for offset, call in enumerate(calls)]


def openai_usage(usage: dict | None) -> dict | None:
    if not usage:
        return None
    prompt, completion = usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}


class Reply:

    def __init__(self, model: str):
        self.id = f"chatcmpl-{uuid.uuid4().hex}"
        self.created = int(time.time())
        self.model = model

    def chunk(self, delta: dict, finish_reason: str | None = None, usage: dict | None = None) -> dict:
        chunk = {"id": self.id, "object": "chat.completion.chunk", "created": self.created, "model": self.model,
                 "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}
        return {**chunk, "usage": usage} if usage else chunk

    def whole(self, text: str, calls: list[dict], usage: dict | None, cut_off: bool = False) -> dict:
        message = {"role": "assistant", "content": text or None}
        if calls:
            message["tool_calls"] = [{k: v for k, v in call.items() if k != "index"} for call in calls]
        whole = {"id": self.id, "object": "chat.completion", "created": self.created, "model": self.model,
                 "choices": [{"index": 0, "message": message, "finish_reason": finish_reason_of(calls, cut_off)}]}
        return {**whole, "usage": usage} if usage else whole


def translated_events(endpoint, api_key: str | None, body: dict, model: str):
    messages, system = split_system(body["messages"], endpoint.provider)
    tools = body.get("tools") if isinstance(body.get("tools"), list) else None
    return stream_chat_once(endpoint.base_url, api_key, model, messages, system, translated_options(body), tools,
                            max_bytes=MAX_REPLY_BYTES)


def log_translated_failure(key: AppKey, model: str) -> None:
    logger.warning("app key %r: the model server for %r failed", key.name, model)


def log_starfire_failure(key: AppKey, model: str) -> None:
    logger.exception("app key %r: starfire failed while answering for %r", key.name, model)


def finish_reason_of(calls: list[dict], cut_off: bool) -> str:
    if calls:
        return "tool_calls"
    return LENGTH_FINISH if cut_off else "stop"


async def translated_stream(events, reply: Reply, key: AppKey, give_back):
    yield sse(reply.chunk({"role": "assistant", "content": ""}))
    calls, usage, said_anything, sent_at, cut_off = [], None, False, time.monotonic(), False
    try:
        async for kind, item in events:
            if kind == "delta":
                said_anything = True
                yield sse(reply.chunk({"content": item}))
            elif kind == "tool_calls":
                said_anything = True
                made = openai_tool_calls(item, len(calls))
                calls += made
                yield sse(reply.chunk({"tool_calls": made}))
            elif kind == "usage":
                usage = openai_usage(item)
            elif kind == CUT_OFF_KIND:
                cut_off = True
            elif kind == REPLY_TOO_LONG:
                yield sse(error_body(TOO_LONG, "upstream_error"))
                return
            elif kind == "error":
                log_translated_failure(key, reply.model)
                if not said_anything and may_give_back_after(item, sent_at):
                    give_back()
                yield sse(error_body(UPSTREAM_FAILED, "upstream_error"))
                return
    except Exception:
        log_starfire_failure(key, reply.model)
        if not said_anything and answered_quickly(sent_at):
            give_back()
        yield sse(error_body(STARFIRE_FAILED, "server_error"))
        return
    yield sse(reply.chunk({}, finish_reason_of(calls, cut_off), usage))
    yield STREAM_DONE


def text_size(text: str) -> int:
    return len(text.encode("utf-8", "surrogatepass"))


async def translated_whole(events, reply: Reply, key: AppKey) -> dict:
    text, calls, usage, size, sent_at, cut_off = [], [], None, 0, time.monotonic(), False
    try:
        async for kind, item in events:
            if kind == "delta":
                text.append(item)
                size += text_size(item)
            elif kind == "tool_calls":
                made = openai_tool_calls(item, len(calls))
                calls += made
                size += text_size(json.dumps(made))
            elif kind == "usage":
                usage = openai_usage(item)
            elif kind == CUT_OFF_KIND:
                cut_off = True
            elif kind == "error":
                log_translated_failure(key, reply.model)
                gives_back = not (text or calls) and may_give_back_after(item, sent_at)
                raise ApiRefusal(502, UPSTREAM_FAILED, "upstream_error", gives_back_reply=gives_back)
            if kind == REPLY_TOO_LONG or size > MAX_REPLY_BYTES:
                raise ApiRefusal(502, TOO_LONG, "upstream_error")
    except ApiRefusal:
        raise
    except Exception as error:
        log_starfire_failure(key, reply.model)
        gives_back = not (text or calls) and answered_quickly(sent_at)
        raise ApiRefusal(500, STARFIRE_FAILED, "server_error", gives_back_reply=gives_back) from error
    finally:
        await events.aclose()
    return reply.whole("".join(text), calls, usage, cut_off)


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    parts = content if isinstance(content, list) else []
    return "\n".join(part["text"] for part in parts
                     if isinstance(part, dict) and part.get("type") == TEXT_PART and isinstance(part.get("text"), str))


def plain_text_messages(messages: list[dict]) -> list[dict]:
    return [{"role": message["role"], "content": text_of(message.get("content"))} for message in messages]


async def arty_rules(endpoint, model: str, body: dict, key: AppKey) -> remote_rules.ArtyRules | None:
    if not remote_rules.applies_to(endpoint, routes._all_endpoints):
        return None
    if problem := remote_rules.message_problem(body["messages"]):
        raise bad_request(problem)
    try:
        return await remote_rules.rules_for(model, plain_text_messages(body["messages"]), caller=caller_of(body),
                                            may_be_unrestricted=routes._lists_model(endpoint, model))
    except (HTTPException, ValueError) as error:
        logger.warning("app key %r: no Arty rules for %r (%s)", key.name, model, type(error).__name__)
        raise ApiRefusal(502, RULES_MISSING, "upstream_error", gives_back_reply=True) from error


def remote_tools_only(tools) -> list[dict]:
    allowed = set(config.remote_tool_names())
    return [remote_rules.ruled_tool(tool) for tool in tools or [] if tool["function"]["name"] in allowed]


def caller_of(body: dict) -> remote_rules.CallerText:
    tools = remote_rules.tool_texts(remote_tools_only(body.get("tools")))
    return remote_rules.caller_text(body["messages"], tools=tools)


def under_arty_rules(body: dict, rules: remote_rules.ArtyRules) -> dict:
    messages = remote_rules.ruled_messages(remote_rules.kept_messages(body["messages"], rules))
    caller_system = "\n\n".join(m["content"] for m in messages if m["role"] in SYSTEM_ROLES)
    system = remote_rules.system_with_rules(rules, caller_system, routes.PRIVATE_INSTRUCTIONS_NOTE)
    ruled = {name: value for name, value in body.items() if name not in ("tools", "tool_choice")}
    ruled["messages"] = [{"role": "system", "content": system},
                         *(m for m in messages if m["role"] not in SYSTEM_ROLES)]
    tools = remote_tools_only(body.get("tools"))
    return {**ruled, "tools": tools} if tools else ruled


async def declined_events(text: str):
    yield "delta", text


async def answer(endpoint, model: str, own_tools: bool, body: dict, key: AppKey, request: Request,
                 cleanups: list) -> Response:
    api_key = routes._api_key_for(endpoint.id, endpoint.provider)
    sent = {**(standard_fields_only(body) if own_tools else body), "model": model}
    said = plain_text_messages(sent["messages"])
    declined = await screen_checks.declined_request(model, said,
                                                   arty_checks_it=remote_rules.applies_to(endpoint, routes._all_endpoints))
    rules = None if declined else await arty_rules(endpoint, model, sent, key)
    if not declined and rules is None and endpoint.provider == OPENAI_PROVIDER:
        return await forward_as_is(endpoint, api_key, sent, key, request, cleanups)
    reply = Reply(body["model"])
    if declined:
        events = declined_events(declined)
    elif rules is None:
        events = content_screen.guarded_events(translated_events(endpoint, api_key, sent, model),
                                            screen_checks.reply_context(said), screen_checks.request_of(said))
    elif rules.declined:
        events = declined_events(rules.declined)
    else:
        events = content_screen.guarded_events(remote_rules.without_em_dashes_in_events(
            remote_rules.without_rules_text_in_events(translated_events(endpoint, api_key, under_arty_rules(sent, rules), model),
                                                      remote_rules.rules_text(rules, caller_of(sent).everything()))),
            screen_checks.reply_context(said), screen_checks.request_of(said))
    if body.get("stream"):
        give_back = functools.partial(auth_routes.app_keys.give_back_reply, key.id)
        return StreamingResponse(while_the_key_works(translated_stream(events, reply, key, give_back), key),
                                 media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
    return json_response(await unless_stopped(translated_whole(events, reply, key), request, key))


async def answer_counted(endpoint, model: str, own_tools: bool, body: dict, key: AppKey, request: Request,
                         cleanups: list) -> Response:
    take_replies(key)
    started_at = time.monotonic()
    try:
        return await answer(endpoint, model, own_tools, body, key, request, cleanups)
    except ApiRefusal as refusal:
        if refusal.gives_back_reply:
            auth_routes.app_keys.give_back_reply(key.id)
        raise
    except Exception as error:
        if answered_quickly(started_at):
            auth_routes.app_keys.give_back_reply(key.id)
        log_starfire_failure(key, model)
        raise ApiRefusal(500, STARFIRE_FAILED, "server_error") from error


class SentThenReleased(Response):

    def __init__(self, inner: Response, slot: ReplySlot, cleanups: list):
        self.inner = inner
        self.slot = slot
        self.cleanups = cleanups
        self.status_code = inner.status_code
        self.background = None

    async def __call__(self, scope, receive, send) -> None:
        try:
            await self.inner(scope, receive, send)
            if self.background is not None:
                await self.background()
        finally:
            await finish_reply(self.slot, self.cleanups)


async def finish_reply(slot: ReplySlot, cleanups: list) -> None:
    try:
        for cleanup in cleanups:
            await cleanup()
    finally:
        slot.release()


async def answer_in_slot(endpoint, model: str, own_tools: bool, body: dict, key: AppKey, request: Request) -> Response:
    slot = running_replies.take(key.id)
    cleanups = []
    answering = asyncio.ensure_future(answer_counted(endpoint, model, own_tools, body, key, request, cleanups))
    try:
        if body.get("stream"):
            done, _ = await asyncio.wait({answering}, timeout=routes.CHECKS_GRACE_SECONDS)
            if not done:
                return SentThenReleased(kept_alive_stream(answering), slot, cleanups)
        response = await answering
    except BaseException:
        if not answering.done():
            answering.cancel()
        await finish_reply(slot, cleanups)
        raise
    return SentThenReleased(response, slot, cleanups)


def kept_alive_stream(answering: asyncio.Future) -> StreamingResponse:
    return StreamingResponse(keep_alives_until(answering), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


async def keep_alives_until(answering: asyncio.Future):
    try:
        while not answering.done():
            yield routes.HEARTBEAT_LINE
            await asyncio.wait({answering}, timeout=routes.HEARTBEAT_SECONDS)
        try:
            response = answering.result()
        except ApiRefusal as refusal:
            yield sse(error_body(refusal.message, refusal.code))
            yield STREAM_DONE
            return
        async for chunk in response.body_iterator:
            yield chunk
    finally:
        if not answering.done():
            answering.cancel()


@openai_router.post("/chat/completions")
async def chat_completions(request: Request):
    model_id = ""
    try:
        key = authenticate(request)
    except ApiRefusal as refusal:
        return refusal_response(refusal)
    try:
        body = await read_request(request)
        model_id = body["model"]
        endpoint, model, own_tools = await owning_connection(key, model_id)
        response = await answer_in_slot(endpoint, model, own_tools, body, key, request)
    except ApiRefusal as refusal:
        log_outcome(key, request.url.path, model_id, refusal.status)
        return refusal_response(refusal)
    log_outcome(key, request.url.path, model_id, response.status_code)
    return response


class GrantBody(BaseModel):
    endpoint_id: str = Field(min_length=1, max_length=MAX_ENDPOINT_ID_CHARS)
    model: str = Field(min_length=1, max_length=MAX_MODEL_NAME_CHARS)


class NewKeyBody(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_KEY_NAME_CHARS)
    user_id: str = Field(min_length=1, max_length=MAX_USER_ID_CHARS)
    grants: list[GrantBody] = Field(min_length=1)
    allow_own_tools: StrictBool = False
    daily_limit: StrictInt | None = None
    expires_in_days: StrictInt | None = None


def require_admin(request: Request) -> None:
    user = getattr(request.state, "user", None)
    if user is None or user.role != ADMIN_ROLE or user.disabled:
        raise HTTPException(403, "only an admin can manage app keys")


def connection_label(endpoint_id: str) -> str:
    return routes._connection_labels(for_admin=True).get(endpoint_id, "")


def key_view(key: AppKey) -> dict:
    account = auth_routes.users.find_by_id(key.user_id)
    grants = [{**grant, "connection": connection_label(grant["endpoint_id"])} for grant in key.grants]
    return {**key.public(), "grants": grants, "account": account.email if account else "",
            "account_open": bool(account) and not account.disabled}


def require_open_account(user_id: str) -> None:
    if open_account(user_id) is None:
        raise HTTPException(404, "no open account with that id")


def offered_to_account(user_id: str, model: str, own_tools: bool) -> bool:
    if own_tools and not is_admin_account(user_id):
        return False
    with user_data.run_as(user_id):
        return routes._may_use_model(model)


def connection_choices(user_id: str, endpoint, state) -> dict:
    own_tools_connection = is_own_tools_connection(endpoint, state)
    models = [{"name": name, "own_tools": own_tools_connection or brings_own_tools(name)}
              for name in model_names(state) if name != EVERY_MODEL]
    return {"endpoint_id": endpoint.id, "label": connection_label(endpoint.id),
            "available": state.available, "own_tools": own_tools_connection,
            "models": [model for model in models if offered_to_account(user_id, model["name"], model["own_tools"])]}


@admin_router.get("")
async def list_keys(request: Request):
    require_admin(request)
    return {"keys": [key_view(key) for key in auth_routes.app_keys.list()]}


@admin_router.get("/models/{user_id}")
async def account_models(request: Request, user_id: str):
    require_admin(request)
    require_open_account(user_id)
    connections = [connection_choices(user_id, endpoint, state) for endpoint, state in await checked_connections()]
    return {"admin": is_admin_account(user_id), "connections": connections}


def grant_problem(grant: GrantBody, body: NewKeyBody) -> str:
    endpoint = routes.endpoints.get(grant.endpoint_id)
    if endpoint is None:
        return f"there is no connection {grant.endpoint_id}"
    state = routes.connection_health.state(endpoint.id)
    own_tools = needs_arty_option(endpoint, state, grant.model)
    named = endpoint.label or endpoint.id if grant.model == EVERY_MODEL else grant.model
    if own_tools and not body.allow_own_tools:
        return f"{named} acts for the account with its own tools: tick the Arty option to allow it"
    if grant.model == EVERY_MODEL:
        return ""
    if not offered_to_account(body.user_id, grant.model, own_tools):
        return f"that account may not use {grant.model}"
    return ""


@admin_router.post("")
async def make_key(request: Request, body: NewKeyBody, response: Response):
    require_admin(request)
    require_open_account(body.user_id)
    if body.allow_own_tools and not is_admin_account(body.user_id):
        raise HTTPException(400, "only a key for an admin account may use models that bring their own tools")
    await checked_connections({grant.endpoint_id for grant in body.grants})
    problems = [problem for problem in (grant_problem(grant, body) for grant in body.grants) if problem]
    if problems:
        raise HTTPException(400, "; ".join(problems))
    try:
        key, secret = auth_routes.app_keys.create(body.name, body.user_id, [grant.model_dump() for grant in body.grants],
                                                  body.daily_limit, body.expires_in_days, body.allow_own_tools)
    except AppKeyError as error:
        raise HTTPException(400, str(error)) from error
    logger.info("app key %r made for account %s", key.name, key.user_id)
    response.headers["Cache-Control"] = "no-store"
    return {**key_view(key), "key": secret}


@admin_router.delete("/{key_id}")
async def revoke_key(request: Request, key_id: str):
    require_admin(request)
    gone = auth_routes.app_keys.revoke(key_id)
    if gone is None:
        raise HTTPException(404, "no app key with that id")
    logger.info("app key %r revoked", gone.name)
    return {"ok": True}
