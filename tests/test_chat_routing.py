import asyncio
import json
import os
import sys

import bcrypt
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import auth
import auth_routes
import chat_stream
import routes
import user_data
from api_key_manager import APIKeyManager
from auth import SessionStore, UserStore
from config import config
from model_endpoints import ModelEndpointStore

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}
PNG = b"\x89PNG fake image bytes"


@pytest.fixture
def model_server(monkeypatch):
    requests = []
    reply = {"status": 200, "body": ""}

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if reply["status"] is None:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(reply["status"], text=reply["body"])

    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(chat_stream.httpx, "AsyncClient", client)
    monkeypatch.setattr(config, "ollama_base_url", None)
    return requests, reply


def stream(base_url, api_key, messages, system=None, options=None):
    async def collect():
        return [chunk async for chunk in chat_stream.stream_chat(base_url, api_key, "the-model", messages, system, options)]
    return [json.loads(chunk.decode()[len("data: "):]) for chunk in asyncio.run(collect())]


def stream_once(base_url, messages, tools):
    async def collect():
        return [item async for item in chat_stream.stream_chat_once(base_url, "k", "m", messages, None, None, tools)]
    return asyncio.run(collect())


HELLO = [{"role": "user", "content": "hello"}]


def test_a_local_ollama_chat_goes_to_ollama_without_a_key_and_streams_text(model_server):
    requests, reply = model_server
    reply["body"] = "\n".join(json.dumps(line) for line in [
        {"message": {"content": "Hi "}}, {"message": {"content": "there"}}, {"done": True}])
    events = stream("http://localhost:11434", None, HELLO, system="Be brief.", options={"temperature": 0.1})
    assert events == [{"delta": "Hi "}, {"delta": "there"}, {"done": True}]
    [request] = requests
    sent = json.loads(request.content)
    assert str(request.url) == "http://localhost:11434/api/chat" and "authorization" not in request.headers
    assert sent["messages"][0] == {"role": "system", "content": "Be brief."}
    assert sent["options"]["temperature"] == 0.1 and sent["stream"] is True


def test_an_anthropic_chat_sends_the_key_in_its_own_header_and_the_system_prompt_apart(model_server):
    requests, reply = model_server
    reply["body"] = "\n".join([
        "event: content_block_delta", 'data: {"index": 0, "delta": {"type": "text_delta", "text": "Hello"}}', "",
        "event: message_stop", "data: {}", ""])
    events = stream("https://api.anthropic.com", "sk-ant-PLACEHOLDER", HELLO, system="Be brief.",
                    options={"temperature": 0.3, "num_predict": 99, "num_ctx": 4096})
    assert events == [{"delta": "Hello"}, {"done": True}]
    [request] = requests
    sent = json.loads(request.content)
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == "sk-ant-PLACEHOLDER" and "authorization" not in request.headers
    assert sent["system"] == "Be brief." and sent["messages"] == HELLO
    assert sent["max_tokens"] == 99 and sent["temperature"] == 0.3 and "num_ctx" not in sent


def test_an_openai_style_chat_sends_a_bearer_key_and_maps_its_options(model_server):
    requests, reply = model_server
    reply["body"] = "\n".join([
        'data: {"choices": [{"delta": {"content": "Yo"}}]}', "data: not json", "data: [DONE]"])
    events = stream("https://models.example.com/v1", "sk-PLACEHOLDER", HELLO, options={"num_predict": 50, "top_k": 3})
    assert events == [{"delta": "Yo"}, {"done": True}]
    [request] = requests
    sent = json.loads(request.content)
    assert str(request.url) == "https://models.example.com/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-PLACEHOLDER"
    assert sent["max_tokens"] == 50 and "top_k" not in sent


def test_a_lookalike_host_is_not_treated_as_anthropic(model_server):
    requests, reply = model_server
    reply["body"] = "data: [DONE]"
    stream("https://api.anthropic.com.attacker.example", "sk-PLACEHOLDER", HELLO)
    [request] = requests
    assert str(request.url).endswith("/v1/chat/completions") and "x-api-key" not in request.headers


def test_an_unreachable_model_server_is_reported_once_and_the_stream_ends(model_server):
    _, reply = model_server
    reply["status"] = None
    events = stream("https://models.example.com/v1", None, HELLO)
    assert len(events) == 1 and "connection refused" in events[0]["error"]


def test_an_error_in_the_middle_of_a_stream_stops_it(model_server):
    _, reply = model_server
    reply["body"] = "\n".join([
        'data: {"choices": [{"delta": {"content": "partial"}}]}', 'data: {"error": {"message": "rate limited"}}'])
    assert stream("https://models.example.com/v1", None, HELLO) == [{"delta": "partial"}, {"error": "rate limited"}]


def test_openai_tool_calls_arrive_whole_even_when_split_across_chunks(model_server):
    _, reply = model_server
    chunks = [
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "manage_notes", "arguments": '{"act'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'ion": "list"}'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 1, "function": {"name": "search_web", "arguments": "{bad"}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]
    reply["body"] = "\n".join(f"data: {json.dumps(c)}" for c in chunks)
    items = stream_once("https://models.example.com/v1", HELLO, [{"type": "function", "function": {"name": "manage_notes"}}])
    assert items == [("tool_calls", [
        {"id": "c1", "name": "manage_notes", "arguments": {"action": "list"}},
        {"id": "call-1", "name": "search_web", "arguments": {}},
    ])]


def test_anthropic_tool_calls_are_built_from_their_json_pieces(model_server):
    requests, reply = model_server
    reply["body"] = "\n".join([
        "event: content_block_start",
        'data: {"index": 1, "content_block": {"type": "tool_use", "id": "tu1", "name": "manage_notes"}}', "",
        "event: content_block_delta",
        'data: {"index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\\"action\\": "}}', "",
        "event: content_block_delta",
        'data: {"index": 1, "delta": {"type": "input_json_delta", "partial_json": "\\"list\\"}"}}', "",
        "event: message_delta", 'data: {"delta": {"stop_reason": "tool_use"}}', "",
        "event: message_stop", "data: {}", ""])
    tools = [{"type": "function", "function": {"name": "manage_notes", "description": "notes",
                                               "parameters": {"type": "object"}}}]
    items = stream_once("https://api.anthropic.com", HELLO, tools)
    assert items == [("tool_calls", [{"id": "tu1", "name": "manage_notes", "arguments": {"action": "list"}}])]
    assert json.loads(requests[0].content)["tools"] == [
        {"name": "manage_notes", "description": "notes", "input_schema": {"type": "object"}}]


def test_an_anthropic_stream_error_is_shown(model_server):
    _, reply = model_server
    reply["body"] = "\n".join(["event: error", 'data: {"error": {"message": "overloaded"}}', ""])
    assert stream("https://api.anthropic.com", "k", HELLO) == [{"error": "overloaded"}]


def test_an_ollama_error_line_is_shown(model_server):
    _, reply = model_server
    reply["body"] = json.dumps({"error": "model not loaded"})
    assert stream("http://localhost:11434", None, HELLO) == [{"error": "Ollama: model not loaded"}]


def test_tool_turns_are_rewritten_for_each_provider():
    history = [
        {"role": "assistant", "content": "checking", "tool_calls": [
            {"id": "c1", "function": {"name": "manage_notes", "arguments": '{"action": "list"}'}},
            {"id": "c2", "function": {"name": "search_web", "arguments": "{broken"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "[]"},
    ]
    anthropic = chat_stream._to_anthropic_messages(history)
    assert anthropic[0]["content"] == [
        {"type": "text", "text": "checking"},
        {"type": "tool_use", "id": "c1", "name": "manage_notes", "input": {"action": "list"}},
        {"type": "tool_use", "id": "c2", "name": "search_web", "input": {}}]
    assert anthropic[1] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "[]"}]}
    ollama = chat_stream._to_ollama_messages(history)
    assert ollama[0]["tool_calls"] == [{"function": {"name": "manage_notes", "arguments": {"action": "list"}}},
                                       {"function": {"name": "search_web", "arguments": {}}}]
    assert ollama[1] == history[1]


@pytest.fixture
def app(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    users, sessions = UserStore(data_dir), SessionStore(data_dir)
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(auth_routes, "known_addresses", auth.KnownAddresses(data_dir))
    monkeypatch.setattr(routes, "registry", user_data.configure(data_dir))
    monkeypatch.setattr(routes, "api_keys", APIKeyManager(data_dir))
    monkeypatch.setattr(routes, "endpoints", ModelEndpointStore(data_dir))
    hashed = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    users.upsert("admin@example.com", "Admin", "admin", hashed)
    users.upsert("sam@example.com", "Sam", "user", hashed)
    application = FastAPI()
    auth.install(application, users, sessions)
    application.include_router(auth_routes.router)
    application.include_router(routes.router)
    return application


def login(app, email):
    client = TestClient(app, base_url="https://testserver", headers=HEADERS)
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    return client


@pytest.fixture
def chat_model(monkeypatch):
    calls = []

    async def plain(base_url, api_key, model, messages, system, options):
        calls.append({"kind": "plain", "api_key": api_key, "messages": messages})
        yield chat_stream._sse_line({"delta": "four words of reply"})
        yield chat_stream._sse_line({"done": True})

    async def with_tools(base_url, api_key, model, messages, system, options, servers, tools, ctx, max_tools=None):
        calls.append({"kind": "tools", "tools": tools, "host": ctx.may_use_host_tools,
                      "shell_approval": ctx.require_shell_approval})
        yield chat_stream._sse_line({"done": True})

    monkeypatch.setattr(routes, "stream_chat", plain)
    monkeypatch.setattr(routes, "run_chat_with_tools", with_tools)
    endpoint = routes.endpoints.add("https://models.example.com/v1", kind="api-key")
    routes.api_keys.save(endpoint.id, "sk-PLACEHOLDER")
    return endpoint, calls


def chat(client, endpoint_id, message, **extra):
    return client.post("/api/chat_stream", json={"endpoint_id": endpoint_id, "model": "gpt-4o",
                                                 "messages": [message], **extra})


def test_a_chat_cannot_attach_another_accounts_file(app, chat_model):
    endpoint, calls = chat_model
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    attachment = admin.post("/api/attachments", files={"file": ("private.png", PNG, "image/png")}).json()
    response = chat(sam, endpoint.id, {"role": "user", "content": "describe it", "attachments": [attachment["id"]]})
    assert response.status_code == 400 and "expired" in response.json()["detail"]
    assert calls == []


@pytest.mark.parametrize("attachment_id", ["../../users.json", "0" * 31 + "/"])
def test_a_chat_cannot_name_a_file_outside_the_attachments_folder(app, chat_model, attachment_id):
    endpoint, calls = chat_model
    sam = login(app, "sam@example.com")
    response = chat(sam, endpoint.id, {"role": "user", "content": "x", "attachments": [attachment_id]})
    assert response.status_code == 400 and "bad attachment id" in response.json()["detail"]
    assert calls == []


def test_a_chat_with_an_own_image_sends_it_to_the_model(app, chat_model):
    endpoint, calls = chat_model
    sam = login(app, "sam@example.com")
    attachment = sam.post("/api/attachments", files={"file": ("cat.png", PNG, "image/png")}).json()
    assert chat(sam, endpoint.id, {"role": "user", "content": "what is it", "attachments": [attachment["id"]]}).status_code == 200
    [call] = calls
    assert call["api_key"] == "sk-PLACEHOLDER"
    assert [part["type"] for part in call["messages"][0]["content"]] == ["text", "image_url"]


def test_a_chat_to_an_unknown_endpoint_is_refused(app, chat_model):
    _, calls = chat_model
    sam = login(app, "sam@example.com")
    assert chat(sam, "missing", {"role": "user", "content": "hi"}).status_code == 404
    assert calls == []


def test_an_ordinary_accounts_chat_never_gets_host_tools_and_shell_needs_approval(app, chat_model):
    endpoint, calls = chat_model
    sam, admin = login(app, "sam@example.com"), login(app, "admin@example.com")
    for client in (sam, admin):
        chat(client, endpoint.id, {"role": "user", "content": "clean up"}, enabled_builtin_tools=["run_shell"])
    assert [(c["host"], c["shell_approval"]) for c in calls] == [(False, True), (True, True)]


def test_a_chat_is_counted_in_that_accounts_usage_only(app, chat_model):
    endpoint, _ = chat_model
    sam, admin = login(app, "sam@example.com"), login(app, "admin@example.com")
    reply = chat(sam, endpoint.id, {"role": "user", "content": "hello there"})
    assert "four words of reply" in reply.text
    assert sam.get("/api/usage").json()["today"]["turns"] == 1
    assert admin.get("/api/usage").json()["today"]["turns"] == 0
