from __future__ import annotations

import functools
import json
import logging
import time
import uuid

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, StrictBool, StrictInt

import auth_routes
import routes
import user_data
from app_keys import (EVERY_MODEL, MAX_ENDPOINT_ID_CHARS, MAX_KEY_NAME_CHARS, MAX_MODEL_NAME_CHARS, AppKey,
                      AppKeyError, key_grants)
from auth import LOCKOUT_SECONDS, LoginLimiter, client_address
from chat_stream import stream_chat_once
from config import config
from connection_health import CHECK_EVERY_SECONDS
from generation_quota import GenerationQuota
from providers import build_chat_url, build_headers

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
MAX_JSON_DEPTH = 64
KEY_RECHECK_SECONDS = 1.0
UPSTREAM_KEY_REFUSED = {401, 403}
PASSED_ON_STATUSES = {400, 404, 409, 413, 422, 429}
SERVER_FAILURE_FROM = 500
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


class ApiRefusal(Exception):

    def __init__(self, status: int, message: str, code: str, headers: dict | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.headers = headers or {}

    @property
    def is_upstream_failure(self) -> bool:
        return self.code == "upstream_error" and self.status >= SERVER_FAILURE_FROM


def error_body(message: str, code: str) -> dict:
    return {"error": {"message": message, "type": code, "code": code}}


def refusal_response(refusal: ApiRefusal) -> JSONResponse:
    return JSONResponse(error_body(refusal.message, refusal.code), status_code=refusal.status, headers=refusal.headers)


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
               if may_offer(key, endpoint, state, name)]
    log_outcome(key, request.url.path, "", 200)
    return {"object": "list", "data": offered}


def nesting_is_within(value, limit: int) -> bool:
    waiting = [(value, 1)]
    while waiting:
        item, depth = waiting.pop()
        if depth > limit:
            return False
        children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
        waiting.extend((child, depth + 1) for child in children)
    return True


def is_tool_call(call) -> bool:
    function = call.get("function") if isinstance(call, dict) else None
    return (isinstance(function, dict) and isinstance(function.get("name"), str)
            and isinstance(function.get("arguments", ""), (str, dict)))


def check_message(index: int, message) -> None:
    if not isinstance(message, dict) or not isinstance(message.get("role"), str):
        raise bad_request(f"message {index} must be a JSON object with a role")
    if not isinstance(message.get("content"), (str, list, type(None))):
        raise bad_request(f"message {index}: content must be text, a list of parts or null")
    calls = message.get("tool_calls")
    if calls is not None and not (isinstance(calls, list) and all(is_tool_call(call) for call in calls)):
        raise bad_request(f"message {index}: tool_calls must be a list of {{function: {{name, arguments}}}} objects")


async def read_request(request: Request) -> dict:
    try:
        body = json.loads(await request.body())
    except (ValueError, UnicodeDecodeError, RecursionError) as error:
        raise bad_request("the request body must be JSON") from error
    if not isinstance(body, dict):
        raise bad_request("the request body must be a JSON object")
    if not nesting_is_within(body, MAX_JSON_DEPTH):
        raise bad_request(f"the request is nested more than {MAX_JSON_DEPTH} levels deep")
    model, messages, tools = body.get("model"), body.get("messages"), body.get("tools")
    if not isinstance(model, str) or not 0 < len(model) <= MAX_ENDPOINT_ID_CHARS + MAX_MODEL_NAME_CHARS + 1:
        raise bad_request("model must be a model id from GET /v1/models")
    if not isinstance(messages, list) or not 0 < len(messages) <= MAX_MESSAGES:
        raise bad_request(f"messages must be a list of 1 to {MAX_MESSAGES} messages")
    for index, message in enumerate(messages):
        check_message(index, message)
    if tools is not None and not (isinstance(tools, list) and all(isinstance(tool, dict) for tool in tools)):
        raise bad_request("tools must be a list of objects")
    if not isinstance(body.get("stream", False), bool):
        raise bad_request("stream must be true or false")
    if body.get("n", 1) != 1:
        raise bad_request("starfire gives one reply per request: leave n out or set it to 1")
    return body


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


async def while_the_key_works(chunks, key: AppKey):
    checked_at = time.monotonic()
    try:
        async for chunk in chunks:
            now = time.monotonic()
            if now - checked_at >= KEY_RECHECK_SECONDS:
                checked_at = now
                if not key_still_works(key):
                    yield sse(error_body(STOPPED, "invalid_api_key"))
                    return
            yield chunk
    finally:
        await chunks.aclose()


def sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


def upstream_refusal(status: int) -> ApiRefusal:
    if status in UPSTREAM_KEY_REFUSED:
        return ApiRefusal(502, "the model server refused starfire's key for it", "upstream_error")
    if status in PASSED_ON_STATUSES:
        return ApiRefusal(status, f"the model server refused this request ({status})", "upstream_refused")
    return ApiRefusal(502, UPSTREAM_FAILED, "upstream_error")


def upstream_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT, transport=upstream_transport)


async def relayed_bytes(client: httpx.AsyncClient, upstream: httpx.Response):
    try:
        async for chunk in upstream.aiter_bytes():
            yield chunk
    finally:
        await upstream.aclose()
        await client.aclose()


def standard_fields_only(body: dict) -> dict:
    kept = {name: value for name, value in body.items() if name in OPENAI_FIELDS}
    kept["messages"] = [{name: value for name, value in message.items() if name in OPENAI_MESSAGE_FIELDS}
                        for message in body["messages"]]
    return kept


async def forward_as_is(endpoint, api_key: str | None, body: dict, key: AppKey) -> Response:
    url, headers = build_chat_url(endpoint.base_url), build_headers(api_key, endpoint.base_url)
    client = upstream_client()
    try:
        upstream = await client.send(client.build_request("POST", url, headers=headers, json=body),
                                     stream=bool(body.get("stream")))
    except httpx.HTTPError as error:
        await client.aclose()
        logger.warning("app key %r: the model server for %r did not answer (%s)", key.name, body["model"],
                       type(error).__name__)
        raise ApiRefusal(502, UPSTREAM_FAILED, "upstream_error") from error
    if not upstream.is_success or not body.get("stream"):
        content = await upstream.aread()
        await upstream.aclose()
        await client.aclose()
        if not upstream.is_success:
            logger.warning("app key %r: the model server for %r answered %d", key.name, body["model"],
                           upstream.status_code)
            raise upstream_refusal(upstream.status_code)
        return Response(content, media_type=upstream.headers.get("content-type", "application/json"))
    return StreamingResponse(while_the_key_works(relayed_bytes(client, upstream), key),
                             media_type=upstream.headers.get("content-type", "text/event-stream"),
                             headers={"Cache-Control": "no-cache"})


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

    def whole(self, text: str, calls: list[dict], usage: dict | None) -> dict:
        message = {"role": "assistant", "content": text or None}
        if calls:
            message["tool_calls"] = [{k: v for k, v in call.items() if k != "index"} for call in calls]
        whole = {"id": self.id, "object": "chat.completion", "created": self.created, "model": self.model,
                 "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if calls else "stop"}]}
        return {**whole, "usage": usage} if usage else whole


def translated_events(endpoint, api_key: str | None, body: dict, model: str):
    messages, system = split_system(body["messages"], endpoint.provider)
    tools = body.get("tools") if isinstance(body.get("tools"), list) else None
    return stream_chat_once(endpoint.base_url, api_key, model, messages, system, translated_options(body), tools)


def log_translated_failure(key: AppKey, model: str) -> None:
    logger.warning("app key %r: the model server for %r failed", key.name, model)


async def translated_stream(events, reply: Reply, key: AppKey, give_back):
    yield sse(reply.chunk({"role": "assistant", "content": ""}))
    calls, usage, said_anything = [], None, False
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
        elif kind == "error":
            log_translated_failure(key, reply.model)
            if not said_anything:
                give_back()
            yield sse(error_body(UPSTREAM_FAILED, "upstream_error"))
            return
    yield sse(reply.chunk({}, "tool_calls" if calls else "stop", usage))
    yield b"data: [DONE]\n\n"


async def translated_whole(events, reply: Reply, key: AppKey) -> dict:
    text, calls, usage = [], [], None
    async for kind, item in events:
        if kind == "delta":
            text.append(item)
        elif kind == "tool_calls":
            calls += openai_tool_calls(item, len(calls))
        elif kind == "usage":
            usage = openai_usage(item)
        elif kind == "error":
            log_translated_failure(key, reply.model)
            raise ApiRefusal(502, UPSTREAM_FAILED, "upstream_error")
    return reply.whole("".join(text), calls, usage)


async def answer(endpoint, model: str, own_tools: bool, body: dict, key: AppKey) -> Response:
    api_key = routes._api_key_for(endpoint.id, endpoint.provider)
    sent = {**(standard_fields_only(body) if own_tools else body), "model": model}
    if endpoint.provider == OPENAI_PROVIDER:
        return await forward_as_is(endpoint, api_key, sent, key)
    reply = Reply(body["model"])
    events = translated_events(endpoint, api_key, sent, model)
    if body.get("stream"):
        give_back = functools.partial(auth_routes.app_keys.give_back_reply, key.id)
        return StreamingResponse(while_the_key_works(translated_stream(events, reply, key, give_back), key),
                                 media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
    return JSONResponse(await translated_whole(events, reply, key))


async def answer_counted(endpoint, model: str, own_tools: bool, body: dict, key: AppKey) -> Response:
    take_replies(key)
    try:
        return await answer(endpoint, model, own_tools, body, key)
    except ApiRefusal as refusal:
        if refusal.is_upstream_failure:
            auth_routes.app_keys.give_back_reply(key.id)
        raise
    except Exception as error:
        auth_routes.app_keys.give_back_reply(key.id)
        logger.exception("app key %r: starfire failed while answering for %r", key.name, model)
        raise ApiRefusal(500, STARFIRE_FAILED, "server_error") from error


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
        response = await answer_counted(endpoint, model, own_tools, body, key)
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
    endpoint = routes.endpoints.get(endpoint_id)
    return (endpoint.label or endpoint.base_url) if endpoint else ""


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
    return {"endpoint_id": endpoint.id, "label": endpoint.label or endpoint.base_url,
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
