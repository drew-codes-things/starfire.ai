from __future__ import annotations

import json
import logging
import time
import uuid

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, StrictInt

import auth_routes
import routes
import user_data
from app_keys import MAX_KEY_NAME_CHARS, MAX_MODEL_NAME_CHARS, AppKey, AppKeyError, AppKeyStore, key_allows_model
from auth import LOCKOUT_SECONDS, LoginLimiter, client_address
from chat_stream import stream_chat_once
from config import config
from providers import build_chat_url, build_headers

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/api/app-keys")
openai_router = APIRouter(prefix="/v1")
app_keys = AppKeyStore(config.data_dir)
BAD_KEY_MAX_FAILURES = 10
bad_key_limiter = LoginLimiter(max_failures=BAD_KEY_MAX_FAILURES, lockout_seconds=LOCKOUT_SECONDS)
upstream_transport: httpx.AsyncBaseTransport | None = None

UPSTREAM_TIMEOUT = httpx.Timeout(600.0, connect=10.0)
BEARER = "bearer"
MAX_MESSAGES = 4096
MAX_ERROR_CHARS = 500
KEY_RECHECK_SECONDS = 1.0
UPSTREAM_KEY_REFUSED = {401, 403}
PASSED_ON_STATUSES = {400, 404, 409, 413, 422, 429}
OPENAI_PROVIDER = "openai"
ANTHROPIC_PROVIDER = "anthropic"
MAX_USER_ID_CHARS = 64
OPTION_NAMES = {"temperature": "temperature", "top_p": "top_p", "top_k": "top_k", "seed": "seed",
                "max_tokens": "num_predict", "max_completion_tokens": "num_predict"}
NOT_VALID = "this app key is not valid: it is wrong, revoked or expired, or its account is closed"
NO_KEY = "send an app key as 'Authorization: Bearer sf_...'"
STOPPED = "the app key was revoked or its account was closed, so the reply was stopped"


class ApiRefusal(Exception):

    def __init__(self, status: int, message: str, code: str, headers: dict | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.headers = headers or {}


def error_body(message: str, code: str) -> dict:
    return {"error": {"message": message, "type": code, "code": code}}


def refusal_response(refusal: ApiRefusal) -> JSONResponse:
    return JSONResponse(error_body(refusal.message, refusal.code), status_code=refusal.status, headers=refusal.headers)


def presented_key(request: Request) -> str:
    scheme, _, value = request.headers.get("authorization", "").strip().partition(" ")
    return value.strip() if scheme.lower() == BEARER else ""


def open_account(user_id: str):
    account = auth_routes.users.find_by_id(user_id)
    return account if account and not account.disabled else None


def caller_address(request: Request) -> str:
    return client_address(request, auth_routes.TRUSTED_PROXIES, config.client_ip_header)


def authenticate(request: Request) -> AppKey:
    address = caller_address(request)
    wait = bad_key_limiter.seconds_locked(address)
    if wait:
        raise ApiRefusal(429, f"too many wrong app keys from this address - try again in {wait} s",
                         "rate_limit_exceeded", {"Retry-After": str(wait)})
    secret = presented_key(request)
    key = app_keys.find(secret) if secret else None
    if key is None or open_account(key.user_id) is None:
        bad_key_limiter.record_failure(address)
        logger.warning("refused an app key request to %s from %s: %s", request.url.path, address,
                       "no key" if not secret else "key not valid")
        raise ApiRefusal(401, NO_KEY if not secret else NOT_VALID, "invalid_api_key")
    return key


def is_allowed(key: AppKey, model: str) -> bool:
    with user_data.run_as(key.user_id):
        return key_allows_model(key, model) and routes._may_use_model(model)


async def usable_models(key: AppKey) -> list[dict]:
    with user_data.run_as(key.user_id):
        listed = await routes._listed_models()
    seen, usable = set(), []
    for model in listed:
        name = model.get("id")
        if not isinstance(name, str) or name in seen or not is_allowed(key, name):
            continue
        seen.add(name)
        usable.append(model)
    return usable


def log_outcome(key: AppKey, path: str, model: str, status: int) -> None:
    logger.info("app key %r: %s model %r answered %d", key.name, path, model, status)


@openai_router.get("/models")
async def list_models(request: Request):
    try:
        key = authenticate(request)
    except ApiRefusal as refusal:
        return refusal_response(refusal)
    models = await usable_models(key)
    log_outcome(key, request.url.path, "", 200)
    return {"object": "list", "data": [{"id": m["id"], "object": "model", "created": 0, "owned_by": "starfire"}
                                       for m in models]}


async def read_request(request: Request) -> dict:
    try:
        body = json.loads(await request.body())
    except (ValueError, UnicodeDecodeError) as error:
        raise ApiRefusal(400, "the request body must be JSON", "invalid_request_error") from error
    if not isinstance(body, dict):
        raise ApiRefusal(400, "the request body must be a JSON object", "invalid_request_error")
    model, messages = body.get("model"), body.get("messages")
    if not isinstance(model, str) or not 0 < len(model) <= MAX_MODEL_NAME_CHARS:
        raise ApiRefusal(400, "model must be a model name from GET /v1/models", "invalid_request_error")
    if not isinstance(messages, list) or not 0 < len(messages) <= MAX_MESSAGES:
        raise ApiRefusal(400, f"messages must be a list of 1 to {MAX_MESSAGES} messages", "invalid_request_error")
    if not all(isinstance(message, dict) for message in messages):
        raise ApiRefusal(400, "each message must be a JSON object", "invalid_request_error")
    if not isinstance(body.get("stream", False), bool):
        raise ApiRefusal(400, "stream must be true or false", "invalid_request_error")
    if body.get("n", 1) != 1:
        raise ApiRefusal(400, "starfire gives one reply per request: leave n out or set it to 1", "invalid_request_error")
    return body


def take_replies(key: AppKey) -> None:
    try:
        reached = app_keys.take_reply(key.id)
    except AppKeyError as error:
        raise ApiRefusal(401, NOT_VALID, "invalid_api_key") from error
    if reached:
        raise ApiRefusal(429, f"this app key has used its {reached.limit} replies for today; the count starts again "
                         "at midnight UTC", "rate_limit_exceeded", {"Retry-After": str(reached.retry_after_seconds)})
    with user_data.run_as(key.user_id):
        refusal = routes._generation_refusal()
    if refusal:
        app_keys.give_back_reply(key.id)
        raise ApiRefusal(429, refusal.message, "rate_limit_exceeded", {"Retry-After": str(refusal.retry_after_seconds)})


async def chosen_endpoint(key: AppKey, model: str):
    if not is_allowed(key, model):
        raise ApiRefusal(403, f"this app key may not use the model {model}", "model_not_allowed")
    listed = next((m for m in await usable_models(key) if m["id"] == model), None)
    endpoint = routes.endpoints.get(listed["endpoint_id"]) if listed else None
    if endpoint is None:
        raise ApiRefusal(404, f"no connection offers the model {model}; GET /v1/models lists what this key may use",
                         "model_not_found")
    return endpoint


def key_still_works(key: AppKey) -> bool:
    return app_keys.is_live(key.id) and open_account(key.user_id) is not None


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


def upstream_refusal(status: int, content: bytes) -> ApiRefusal:
    text = content.decode(errors="replace")[:MAX_ERROR_CHARS] or "no details"
    if status in UPSTREAM_KEY_REFUSED:
        return ApiRefusal(502, "the model server refused starfire's key for it", "upstream_error")
    if status in PASSED_ON_STATUSES:
        return ApiRefusal(status, f"the model server answered {status}: {text}", "upstream_error")
    return ApiRefusal(502, f"the model server answered {status}: {text}", "upstream_error")


def upstream_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT, transport=upstream_transport)


async def relayed_bytes(client: httpx.AsyncClient, upstream: httpx.Response):
    try:
        async for chunk in upstream.aiter_bytes():
            yield chunk
    finally:
        await upstream.aclose()
        await client.aclose()


async def forward_as_is(endpoint, api_key: str | None, body: dict, key: AppKey) -> Response:
    url, headers = build_chat_url(endpoint.base_url), build_headers(api_key, endpoint.base_url)
    client = upstream_client()
    try:
        upstream = await client.send(client.build_request("POST", url, headers=headers, json=body),
                                     stream=bool(body.get("stream")))
    except httpx.HTTPError as error:
        await client.aclose()
        logger.warning("app key %r: model server %s did not answer: %s", key.name, endpoint.base_url, type(error).__name__)
        raise ApiRefusal(502, "the model server did not answer", "upstream_error") from error
    if upstream.is_error or not body.get("stream"):
        content = await upstream.aread()
        await upstream.aclose()
        await client.aclose()
        if upstream.is_error:
            raise upstream_refusal(upstream.status_code, content)
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


def translated_events(endpoint, api_key: str | None, body: dict):
    messages, system = split_system(body["messages"], endpoint.provider)
    tools = body.get("tools") if isinstance(body.get("tools"), list) else None
    return stream_chat_once(endpoint.base_url, api_key, body["model"], messages, system, translated_options(body), tools)


async def translated_stream(events, reply: Reply):
    yield sse(reply.chunk({"role": "assistant", "content": ""}))
    calls, usage = [], None
    async for kind, item in events:
        if kind == "delta":
            yield sse(reply.chunk({"content": item}))
        elif kind == "tool_calls":
            made = openai_tool_calls(item, len(calls))
            calls += made
            yield sse(reply.chunk({"tool_calls": made}))
        elif kind == "usage":
            usage = openai_usage(item)
        elif kind == "error":
            yield sse(error_body(str(item)[:MAX_ERROR_CHARS], "upstream_error"))
            return
    yield sse(reply.chunk({}, "tool_calls" if calls else "stop", usage))
    yield b"data: [DONE]\n\n"


async def translated_whole(events, reply: Reply) -> dict:
    text, calls, usage = [], [], None
    async for kind, item in events:
        if kind == "delta":
            text.append(item)
        elif kind == "tool_calls":
            calls += openai_tool_calls(item, len(calls))
        elif kind == "usage":
            usage = openai_usage(item)
        elif kind == "error":
            raise ApiRefusal(502, f"the model server failed: {str(item)[:MAX_ERROR_CHARS]}", "upstream_error")
    return reply.whole("".join(text), calls, usage)


async def answer(endpoint, body: dict, key: AppKey) -> Response:
    api_key = routes._api_key_for(endpoint.id, endpoint.provider)
    if endpoint.provider == OPENAI_PROVIDER:
        return await forward_as_is(endpoint, api_key, body, key)
    reply = Reply(body["model"])
    events = translated_events(endpoint, api_key, body)
    if body.get("stream"):
        return StreamingResponse(while_the_key_works(translated_stream(events, reply), key),
                                 media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
    return JSONResponse(await translated_whole(events, reply))


@openai_router.post("/chat/completions")
async def chat_completions(request: Request):
    model = ""
    try:
        key = authenticate(request)
    except ApiRefusal as refusal:
        return refusal_response(refusal)
    try:
        body = await read_request(request)
        model = body["model"]
        endpoint = await chosen_endpoint(key, model)
        take_replies(key)
        response = await answer(endpoint, body, key)
    except ApiRefusal as refusal:
        log_outcome(key, request.url.path, model, refusal.status)
        return refusal_response(refusal)
    log_outcome(key, request.url.path, model, response.status_code)
    return response


class NewKeyBody(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_KEY_NAME_CHARS)
    user_id: str = Field(min_length=1, max_length=MAX_USER_ID_CHARS)
    models: list[str] | None = None
    daily_limit: StrictInt | None = None
    expires_in_days: StrictInt | None = None


def require_admin(request: Request) -> None:
    user = getattr(request.state, "user", None)
    if user is None or user.role != "admin" or user.disabled:
        raise HTTPException(403, "only an admin can manage app keys")


def key_view(key: AppKey) -> dict:
    account = auth_routes.users.find_by_id(key.user_id)
    return {**key.public(), "account": account.email if account else "",
            "account_open": bool(account) and not account.disabled}


async def models_for_account(user_id: str) -> list[str]:
    with user_data.run_as(user_id):
        return list(dict.fromkeys(m["id"] for m in await routes._usable_models()))


def require_open_account(user_id: str) -> None:
    if open_account(user_id) is None:
        raise HTTPException(404, "no open account with that id")


@admin_router.get("")
async def list_keys(request: Request):
    require_admin(request)
    return {"keys": [key_view(key) for key in app_keys.list()]}


@admin_router.get("/models/{user_id}")
async def account_models(request: Request, user_id: str):
    require_admin(request)
    require_open_account(user_id)
    return {"models": await models_for_account(user_id)}


@admin_router.post("")
async def make_key(request: Request, body: NewKeyBody, response: Response):
    require_admin(request)
    require_open_account(body.user_id)
    with user_data.run_as(body.user_id):
        refused = [model for model in body.models or [] if not routes._may_use_model(model)]
    if refused:
        raise HTTPException(400, f"that account may not use {', '.join(refused)}")
    try:
        key, secret = app_keys.create(body.name, body.user_id, body.models, body.daily_limit, body.expires_in_days)
    except AppKeyError as error:
        raise HTTPException(400, str(error)) from error
    logger.info("app key %r made for account %s", key.name, key.user_id)
    response.headers["Cache-Control"] = "no-store"
    return {**key_view(key), "key": secret}


@admin_router.delete("/{key_id}")
async def revoke_key(request: Request, key_id: str):
    require_admin(request)
    gone = app_keys.revoke(key_id)
    if gone is None:
        raise HTTPException(404, "no app key with that id")
    logger.info("app key %r revoked", gone.name)
    return {"ok": True}
