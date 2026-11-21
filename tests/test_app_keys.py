import asyncio
import io
import json
import logging
import os
import sys
import zipfile

import bcrypt
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import app_key_api
import auth
import auth_routes
import routes
import upload_limits
import user_data
from api_key_manager import APIKeyManager
from app_keys import AppKeyStore, key_digest
from auth import LoginLimiter, SessionStore, UserStore
from config import config
from connection_health import ConnectionHealth
from generation_quota import GenerationQuota
from model_access import ModelAccessStore
from model_endpoints import ModelEndpointStore

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}
FRIEND_URL = "https://friend.drew-gnr.xyz/v1"
FRIEND_KEY = "llama-server-key-for-tests"
LISTED = ["friend-model", "other-model", "example-9b-abl", "Arty"]
OTHER_URL = "https://local.example.com/v1"
NESTED_TOO_DEEP = json.loads("[" * 80 + "]" * 80)
PROMPT = "a private question about the garden"
TOOLS = [{"type": "function", "function": {"name": "read_file", "description": "Read a file",
                                           "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                                                          "required": ["path"]}}}]
STREAMED_TOOL_CALL = (
    b'data: {"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant",'
    b'"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"read_file","arguments":""}}]},'
    b'"finish_reason":null}]}\n\n'
    b'data: {"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,'
    b'"function":{"arguments":"{\\"path\\": \\"a.txt\\"}"}}]},"finish_reason":"tool_calls"}]}\n\n'
    b"data: [DONE]\n\n"
)
WHOLE_TOOL_CALL = {"id": "c2", "object": "chat.completion", "model": "friend-model",
                   "choices": [{"index": 0, "finish_reason": "tool_calls",
                                "message": {"role": "assistant", "content": None,
                                            "tool_calls": [{"id": "call_2", "type": "function",
                                                            "function": {"name": "read_file",
                                                                         "arguments": "{\"path\": \"b.txt\"}"}}]}}]}


class FakeClock:

    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


class FakeModelServers:

    def __init__(self):
        self.chats = []
        self.ollama_lines = []
        self.model_lists = 0
        self.friend_down = None

    def friend_failure(self, request: httpx.Request) -> httpx.Response:
        if self.friend_down == "refused":
            raise httpx.ConnectError("connection refused", request=request)
        if self.friend_down == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(int(self.friend_down), json={"error": "down"})

    def answer(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if self.friend_down and request.url.host == "friend.drew-gnr.xyz":
            return self.friend_failure(request)
        if path.endswith("/models") or path.endswith("/api/tags"):
            self.model_lists += 1
        if path.endswith("/api/tags"):
            return httpx.Response(200, json={"models": [{"name": "local-ollama:8b"}]})
        if path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": name} for name in LISTED]})
        sent = json.loads(request.content)
        self.chats.append({"url": str(request.url), "auth": request.headers.get("authorization"), "body": sent})
        if path.endswith("/api/chat"):
            return httpx.Response(200, content=b"".join(json.dumps(line).encode() + b"\n" for line in self.ollama_lines))
        if sent.get("stream"):
            return httpx.Response(200, content=STREAMED_TOOL_CALL, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=WHOLE_TOOL_CALL)


@pytest.fixture
def servers(monkeypatch):
    fake = FakeModelServers()
    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs.pop("transport", None)
        return real_client(*args, transport=httpx.MockTransport(fake.answer), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    return fake


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def app(tmp_path, monkeypatch, clock):
    data_dir = str(tmp_path)
    users, sessions = UserStore(data_dir), SessionStore(data_dir)
    monkeypatch.setattr(config, "data_dir", data_dir)
    monkeypatch.setattr(config, "ollama_base_url", None)
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(auth_routes, "known_addresses", auth.KnownAddresses(data_dir))
    monkeypatch.setattr(routes, "registry", user_data.configure(data_dir))
    monkeypatch.setattr(routes, "api_keys", APIKeyManager(data_dir))
    monkeypatch.setattr(routes, "endpoints", ModelEndpointStore(data_dir))
    monkeypatch.setattr(routes, "model_access_store", ModelAccessStore(data_dir))
    monkeypatch.setattr(routes, "generation_quota", GenerationQuota())
    monkeypatch.setattr(auth_routes, "app_keys", AppKeyStore(data_dir, clock=clock))
    monkeypatch.setattr(app_key_api, "key_minutes", GenerationQuota(clock=clock))
    monkeypatch.setattr(routes, "connection_health", ConnectionHealth(
        lambda: routes.endpoints.list(), lambda e: routes._api_key_for(e.id, e.provider), clock=clock))
    monkeypatch.setattr(app_key_api, "bad_key_limiter", LoginLimiter(max_failures=app_key_api.BAD_KEY_MAX_FAILURES))
    hashed = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    users.upsert("admin@example.com", "Admin", "admin", hashed)
    users.upsert("sam@example.com", "Sam", "user", hashed)
    application = FastAPI()
    upload_limits.install(application, config)
    auth.install(application, users, sessions)
    application.include_router(auth_routes.router)
    application.include_router(routes.router)
    application.include_router(app_key_api.admin_router)
    application.include_router(app_key_api.openai_router)
    return application


def login(app, email):
    client = TestClient(app, base_url="https://testserver", headers=HEADERS)
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    client.user_id = auth_routes.users.find_by_email(email).id
    return client


@pytest.fixture
def admin(app):
    return login(app, "admin@example.com")


@pytest.fixture
def friend(admin, servers):
    added = admin.post("/api/model-endpoints", json={"base_url": FRIEND_URL, "api_key": FRIEND_KEY, "kind": "api-key",
                                                     "label": "Other (OpenAI-compatible)"})
    assert added.status_code == 200, added.text
    return added.json()


def user_id(email):
    return auth_routes.users.find_by_email(email).id


def every_connection():
    return [{"endpoint_id": endpoint.id, "model": "*"} for endpoint in routes.endpoints.list()]


def new_key(admin, email="sam@example.com", **details):
    grants = details.pop("grants", None)
    return admin.post("/api/app-keys", json={"name": details.pop("name", "Arty phone"), "user_id": user_id(email),
                                             "grants": every_connection() if grants is None else grants, **details})


def make_key(admin, email="sam@example.com", **details):
    made = new_key(admin, email, **details)
    assert made.status_code == 200, made.text
    return made.json()


def connection_of(name):
    wants_ollama = name == "local-ollama:8b"
    return next(e for e in routes.endpoints.list() if (e.provider == "ollama") == wants_ollama)


def model_id(name, endpoint=None):
    return f"{(endpoint or connection_of(name)).id}/{name}"


def outside(app, address=("testclient", 50000), headers=None):
    return TestClient(app, base_url="https://testserver", client=address, headers=headers or {})


def bearer(secret):
    return {"Authorization": f"Bearer {secret}"}


def ask(app, secret, model="friend-model", stream=False, **extra):
    wanted = model if "/" in model else model_id(model)
    return outside(app).post("/v1/chat/completions", headers=bearer(secret),
                             json={"model": wanted, "stream": stream, "messages": [{"role": "user", "content": PROMPT}],
                                   **extra})


def test_a_new_key_is_shown_once_and_only_its_hash_is_stored(app, admin, friend, tmp_path):
    made = make_key(admin)
    secret = made["key"]
    assert secret.startswith("sf_") and len(secret) >= 3 + 43
    stored = (tmp_path / "app_keys.json").read_text()
    assert secret not in stored and key_digest(secret) in stored
    assert oct((tmp_path / "app_keys.json").stat().st_mode & 0o777) == "0o600"
    listed = admin.get("/api/app-keys").json()["keys"]
    assert [k["name"] for k in listed] == ["Arty phone"]
    assert secret not in json.dumps(listed) and key_digest(secret) not in json.dumps(listed)
    assert "key" not in listed[0] and "digest" not in listed[0]
    assert listed[0]["grants"] == [{"endpoint_id": friend["id"], "model": "*", "connection": "Other (OpenAI-compatible)"}]


def test_the_made_key_answer_is_never_cached(admin, friend):
    assert new_key(admin).headers["cache-control"] == "no-store"


def test_two_keys_are_never_the_same(admin, friend):
    assert make_key(admin)["key"] != make_key(admin)["key"]


def test_a_key_needs_at_least_one_grant(admin, friend):
    assert new_key(admin, grants=[]).status_code == 422
    assert new_key(admin, grants=[{"endpoint_id": "nosuchconnection", "model": "*"}]).status_code == 400


def test_only_an_admin_manages_app_keys(app, admin, friend):
    sam = login(app, "sam@example.com")
    assert sam.get("/api/app-keys").status_code == 403
    assert sam.post("/api/app-keys", json={"name": "mine", "user_id": sam.user_id, "grants": every_connection()}
                    ).status_code == 403
    assert sam.get(f"/api/app-keys/models/{sam.user_id}").status_code == 403
    secret_id = make_key(admin)["id"]
    assert sam.delete(f"/api/app-keys/{secret_id}").status_code == 403


def test_the_form_lists_each_connection_with_the_models_the_account_may_use(admin, friend):
    offered = admin.get(f"/api/app-keys/models/{user_id('sam@example.com')}").json()
    assert offered["admin"] is False
    assert offered["connections"] == [{"endpoint_id": friend["id"], "label": "Other (OpenAI-compatible)", "available": True,
                                       "models": [{"name": "friend-model", "own_tools": False},
                                                  {"name": "other-model", "own_tools": False}]}]
    mine = admin.get(f"/api/app-keys/models/{user_id('admin@example.com')}").json()
    assert {"name": "Arty", "own_tools": True} in mine["connections"][0]["models"]


def test_v1_models_ids_name_their_connection(app, admin, friend):
    secret = make_key(admin)["key"]
    listed = outside(app).get("/v1/models", headers=bearer(secret))
    assert listed.status_code == 200
    assert listed.json() == {"object": "list", "data": [
        {"id": f"{friend['id']}/friend-model", "object": "model", "created": 0, "owned_by": "starfire", "available": True},
        {"id": f"{friend['id']}/other-model", "object": "model", "created": 0, "owned_by": "starfire", "available": True}]}


def test_a_key_with_one_model_lists_only_that(app, admin, friend):
    secret = make_key(admin, grants=[{"endpoint_id": friend["id"], "model": "friend-model"}])["key"]
    assert [m["id"] for m in outside(app).get("/v1/models", headers=bearer(secret)).json()["data"]] == [
        model_id("friend-model")]


def test_a_model_outside_the_key_grants_is_refused(app, admin, friend, servers):
    secret = make_key(admin, grants=[{"endpoint_id": friend["id"], "model": "friend-model"}])["key"]
    refused = ask(app, secret, model="other-model")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "model_not_allowed"
    assert servers.chats == []


def test_a_bare_model_name_is_not_a_model_id(app, admin, friend, servers):
    secret = make_key(admin)["key"]
    refused = outside(app).post("/v1/chat/completions", headers=bearer(secret),
                                json={"model": "friend-model", "messages": [{"role": "user", "content": "hi"}]})
    assert refused.status_code == 404 and refused.json()["error"]["code"] == "model_not_found"
    assert servers.chats == []


def test_a_restricted_model_is_refused_to_an_ordinary_account_key(app, admin, friend, servers):
    secret = make_key(admin)["key"]
    assert ask(app, secret, model="example-9b-abl").status_code == 403
    assert servers.chats == []


def test_a_key_for_an_ordinary_account_cannot_be_given_a_restricted_model(admin, friend):
    refused = new_key(admin, grants=[{"endpoint_id": friend["id"], "model": "example-9b-abl"}])
    assert refused.status_code == 400 and "example-9b-abl" in refused.json()["detail"]


def test_an_admin_account_key_may_use_a_restricted_model(app, admin, friend, servers):
    secret = make_key(admin, email="admin@example.com")["key"]
    assert ask(app, secret, model="example-9b-abl").status_code == 200


def test_arty_is_left_out_of_every_key_by_default(app, admin, friend, servers):
    secret = make_key(admin, email="admin@example.com")["key"]
    assert model_id("Arty") not in [m["id"] for m in outside(app).get("/v1/models", headers=bearer(secret)).json()["data"]]
    refused = ask(app, secret, model="Arty")
    assert refused.status_code == 403
    assert servers.chats == []


def test_arty_cannot_be_named_in_a_key_without_the_opt_in(admin, friend):
    refused = new_key(admin, email="admin@example.com", grants=[{"endpoint_id": friend["id"], "model": "Arty"}])
    assert refused.status_code == 400 and "Arty" in refused.json()["detail"]


def test_only_an_admin_account_key_may_opt_in_to_arty(admin, friend):
    refused = new_key(admin, allow_own_tools=True)
    assert refused.status_code == 400


def test_an_opted_in_key_reaches_arty_with_standard_fields_only(app, admin, friend, servers):
    secret = make_key(admin, email="admin@example.com", allow_own_tools=True)["key"]
    assert model_id("Arty") in [m["id"] for m in outside(app).get("/v1/models", headers=bearer(secret)).json()["data"]]
    answered = outside(app).post("/v1/chat/completions", headers=bearer(secret), json={
        "model": model_id("Arty"), "messages": [{"role": "user", "content": PROMPT, "arty_note": "x"}], "tools": TOOLS,
        "temperature": 0.2, "arty_client_tools": True, "arty_reasoning": "high", "client": "probe", "features": ["all"]})
    assert answered.status_code == 200
    sent = servers.chats[-1]["body"]
    assert set(sent) == {"model", "messages", "tools", "temperature"}
    assert sent["model"] == "Arty" and sent["messages"] == [{"role": "user", "content": PROMPT}]


def test_an_arty_key_stops_working_for_arty_when_its_account_is_no_longer_admin(app, admin, friend, servers):
    users = auth_routes.users
    users.upsert("second@example.com", "Second", "admin", users.find_by_email("admin@example.com").password_hash)
    secret = make_key(admin, email="second@example.com", allow_own_tools=True)["key"]
    assert admin.patch(f"/api/auth/users/{user_id('second@example.com')}", json={"role": "user"}).status_code == 200
    assert ask(app, secret, model="Arty").status_code == 403


def test_other_fields_still_pass_through_for_plain_models(app, admin, friend, servers):
    secret = make_key(admin)["key"]
    assert ask(app, secret, top_k=5, repeat_penalty=1.1).status_code == 200
    assert servers.chats[-1]["body"]["top_k"] == 5 and servers.chats[-1]["body"]["model"] == "friend-model"


def test_a_wrong_or_missing_key_is_refused(app, friend, servers):
    for headers in ({}, bearer("sf_" + "A" * 43), bearer(PASSWORD), {"Authorization": "Basic abc"}):
        refused = outside(app).get("/v1/models", headers=headers)
        assert refused.status_code == 401 and refused.json()["error"]["code"] == "invalid_api_key"
    assert servers.chats == []


def test_a_signed_in_session_cookie_does_not_open_the_api(app, admin, friend, servers):
    refused = admin.post("/v1/chat/completions", json={"model": model_id("friend-model"),
                                                       "messages": [{"role": "user", "content": "hi"}]})
    assert refused.status_code == 401
    assert admin.get("/v1/models").status_code == 401
    assert servers.chats == []


def test_other_v1_paths_are_not_served(app, admin):
    assert admin.get("/v1/index.html").status_code == 404
    assert outside(app).get("/v1/embeddings").status_code == 404


def test_a_revoked_key_is_refused_at_once(app, admin, friend):
    made = make_key(admin)
    assert ask(app, made["key"]).status_code == 200
    assert admin.delete(f"/api/app-keys/{made['id']}").status_code == 200
    assert ask(app, made["key"]).status_code == 401
    assert admin.get("/api/app-keys").json()["keys"] == []


def test_an_expired_key_is_refused(app, admin, friend, clock):
    secret = make_key(admin, expires_in_days=1)["key"]
    assert ask(app, secret).status_code == 200
    clock.now += 24 * 3600
    assert ask(app, secret).status_code == 401


def test_disabling_an_account_revokes_its_keys(app, admin, friend):
    secret = make_key(admin)["key"]
    sam = user_id("sam@example.com")
    assert admin.patch(f"/api/auth/users/{sam}", json={"disabled": True}).status_code == 200
    assert ask(app, secret).status_code == 401
    assert admin.patch(f"/api/auth/users/{sam}", json={"disabled": False}).status_code == 200
    assert ask(app, secret).status_code == 401
    assert admin.get("/api/app-keys").json()["keys"] == []


def test_resetting_a_password_revokes_the_account_keys(app, admin, friend):
    secret = make_key(admin)["key"]
    kept = make_key(admin, email="admin@example.com")["key"]
    reset = admin.post(f"/api/auth/users/{user_id('sam@example.com')}/password", json={"password": "another long password"})
    assert reset.status_code == 200
    assert ask(app, secret).status_code == 401
    assert ask(app, kept).status_code == 200


def test_deleting_an_account_removes_its_keys(app, admin, friend):
    secret = make_key(admin)["key"]
    assert admin.delete(f"/api/auth/users/{user_id('sam@example.com')}").status_code == 200
    assert ask(app, secret).status_code == 401
    assert admin.get("/api/app-keys").json()["keys"] == []


def test_a_key_daily_limit_answers_429(app, admin, friend, servers):
    secret = make_key(admin, daily_limit=2)["key"]
    assert [ask(app, secret).status_code for _ in range(2)] == [200, 200]
    refused = ask(app, secret)
    assert refused.status_code == 429 and int(refused.headers["retry-after"]) > 0
    assert len(servers.chats) == 2


def test_a_key_over_its_limit_makes_no_model_list_requests(app, admin, friend, servers):
    secret = make_key(admin, daily_limit=1)["key"]
    assert ask(app, secret).status_code == 200
    lists = servers.model_lists
    assert [ask(app, secret).status_code for _ in range(5)] == [429] * 5
    assert servers.model_lists == lists


def test_the_key_daily_limit_starts_again_the_next_day(app, admin, friend, clock):
    secret = make_key(admin, daily_limit=1)["key"]
    assert ask(app, secret).status_code == 200
    assert ask(app, secret).status_code == 429
    clock.now += 24 * 3600
    assert ask(app, secret).status_code == 200


def test_the_account_daily_reply_count_applies_to_its_keys(app, admin, friend, monkeypatch):
    monkeypatch.setattr(config, "generations_per_day", 1)
    secret = make_key(admin)["key"]
    assert ask(app, secret).status_code == 200
    assert ask(app, secret).status_code == 429


def test_every_key_has_a_per_minute_limit_even_on_an_admin_account(app, admin, friend, monkeypatch, clock):
    monkeypatch.setattr(config, "app_key_requests_per_minute", 3)
    secret = make_key(admin, email="admin@example.com")["key"]
    assert outside(app).get("/v1/models", headers=bearer(secret)).status_code == 200
    assert [ask(app, secret).status_code for _ in range(3)] == [200, 200, 429]
    assert int(ask(app, secret).headers["retry-after"]) > 0
    clock.now += 60
    assert ask(app, secret).status_code == 200


def test_last_used_is_shown_after_a_reply(app, admin, friend, clock):
    secret = make_key(admin)["key"]
    assert admin.get("/api/app-keys").json()["keys"][0]["last_used"] is None
    ask(app, secret)
    assert admin.get("/api/app-keys").json()["keys"][0]["last_used"] == clock.now


def test_wrong_keys_lock_out_wrong_keys_but_never_a_right_key(app, admin, friend):
    secret = make_key(admin)["key"]
    for _ in range(app_key_api.BAD_KEY_MAX_FAILURES):
        assert outside(app).get("/v1/models", headers=bearer("sf_" + "B" * 43)).status_code == 401
    locked = outside(app).get("/v1/models", headers=bearer("sf_" + "B" * 43))
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0
    assert outside(app).get("/v1/models", headers=bearer(secret)).status_code == 200
    assert ask(app, secret).status_code == 200


def test_requests_without_a_key_are_not_counted(app, admin, friend):
    for _ in range(app_key_api.BAD_KEY_MAX_FAILURES * 3):
        assert outside(app).get("/v1/models").status_code == 401
    assert outside(app).get("/v1/models", headers=bearer("sf_" + "D" * 43)).status_code == 401


def test_the_lockout_follows_the_visitor_address_behind_a_trusted_proxy(app, admin, friend):
    proxy = ("127.0.0.1", 40000)
    for _ in range(app_key_api.BAD_KEY_MAX_FAILURES):
        outside(app, proxy, {"CF-Connecting-IP": "203.0.113.5"}).get("/v1/models", headers=bearer("sf_" + "C" * 43))
    assert outside(app, proxy, {"CF-Connecting-IP": "203.0.113.5"}).get(
        "/v1/models", headers=bearer("sf_" + "C" * 43)).status_code == 429
    assert outside(app, proxy, {"CF-Connecting-IP": "203.0.113.6"}).get(
        "/v1/models", headers=bearer("sf_" + "C" * 43)).status_code == 401


def test_a_streamed_tool_call_passes_through_untouched(app, admin, friend, servers):
    secret = make_key(admin)["key"]
    answered = ask(app, secret, stream=True, tools=TOOLS, tool_choice="auto")
    assert answered.status_code == 200
    assert answered.content == STREAMED_TOOL_CALL
    sent = servers.chats[-1]
    assert sent["url"] == FRIEND_URL + "/chat/completions"
    assert sent["auth"] == f"Bearer {FRIEND_KEY}"
    assert sent["body"]["tools"] == TOOLS and sent["body"]["tool_choice"] == "auto"
    assert sent["body"]["messages"] == [{"role": "user", "content": PROMPT}]


def test_a_whole_reply_with_a_tool_call_passes_through_untouched(app, admin, friend, servers):
    secret = make_key(admin)["key"]
    history = [{"role": "user", "content": PROMPT},
               {"role": "assistant", "content": None, "tool_calls": WHOLE_TOOL_CALL["choices"][0]["message"]["tool_calls"]},
               {"role": "tool", "tool_call_id": "call_2", "content": "file text"}]
    answered = outside(app).post("/v1/chat/completions", headers=bearer(secret),
                                 json={"model": model_id("friend-model"), "messages": history, "tools": TOOLS})
    assert answered.status_code == 200 and answered.json() == WHOLE_TOOL_CALL
    assert servers.chats[-1]["body"]["messages"] == history


def test_an_ollama_connection_answers_in_openai_form(app, admin, servers):
    routes.endpoints.add("http://localhost:11434", kind="ollama")
    servers.ollama_lines = [
        {"message": {"role": "assistant", "content": "Let me look."}, "done": False},
        {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "read_file", "arguments": {"path": "c.txt"}}}]}, "done": False},
        {"message": {"role": "assistant", "content": ""}, "done": True, "prompt_eval_count": 7, "eval_count": 3},
    ]
    secret = make_key(admin, email="admin@example.com")["key"]
    answered = ask(app, secret, model="local-ollama:8b", tools=TOOLS)
    assert answered.status_code == 200, answered.text
    choice = answered.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] == "Let me look."
    assert choice["message"]["tool_calls"][0]["function"] == {"name": "read_file", "arguments": "{\"path\": \"c.txt\"}"}
    assert answered.json()["usage"] == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
    assert servers.chats[-1]["body"]["tools"] == TOOLS
    streamed = ask(app, secret, model="local-ollama:8b", stream=True, tools=TOOLS)
    events = [line[len("data: "):] for line in streamed.text.split("\n\n") if line.startswith("data: ")]
    assert events[-1] == "[DONE]"
    chunks = [json.loads(event) for event in events[:-1]]
    assert chunks[1]["choices"][0]["delta"] == {"content": "Let me look."}
    assert chunks[2]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "read_file"
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_an_ollama_error_text_is_not_shown_and_the_reply_is_given_back(app, admin, servers):
    routes.endpoints.add("http://localhost:11434", kind="ollama")
    servers.ollama_lines = [{"error": "unauthorized: token Bearer FAKE-OLLAMA-KEY at http://10.0.0.5:11434"}]
    secret = make_key(admin, email="admin@example.com", daily_limit=1)["key"]
    for stream in (False, True, False):
        answered = ask(app, secret, model="local-ollama:8b", stream=stream)
        assert "FAKE-OLLAMA-KEY" not in answered.text and "10.0.0.5" not in answered.text
    assert answered.status_code == 502


def test_a_refused_upstream_key_is_not_reported_as_a_bad_app_key(app, admin, friend, servers, monkeypatch):
    secret = make_key(admin)["key"]
    check_connections()
    monkeypatch.setattr(servers, "answer", lambda request: httpx.Response(401, json={"error": f"bad key {FRIEND_KEY}"}))
    refused = ask(app, secret)
    assert refused.status_code == 502 and FRIEND_KEY not in refused.text


def test_upstream_error_text_never_reaches_the_key_holder(app, admin, friend, servers, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    secret = make_key(admin)["key"]
    check_connections()
    echo = f"you sent Authorization: Bearer {FRIEND_KEY} and {PROMPT}"
    monkeypatch.setattr(servers, "answer", lambda request: httpx.Response(400, json={"error": {"message": echo}}))
    refused = ask(app, secret)
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "upstream_refused"
    assert FRIEND_KEY not in refused.text and PROMPT not in refused.text
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert FRIEND_KEY not in logged and PROMPT not in logged


def test_a_failing_model_server_does_not_use_up_the_daily_replies(app, admin, friend, servers, monkeypatch):
    secret = make_key(admin, daily_limit=1)["key"]
    check_connections()
    working = servers.answer
    monkeypatch.setattr(servers, "answer", lambda request: httpx.Response(500, json={"error": "boom"}))
    assert [ask(app, secret).status_code for _ in range(3)] == [502, 502, 502]
    monkeypatch.setattr(servers, "answer", working)
    assert ask(app, secret).status_code == 200
    assert ask(app, secret).status_code == 429


def test_more_than_one_reply_per_request_is_refused(app, admin, friend, servers):
    secret = make_key(admin)["key"]
    assert ask(app, secret, n=3).status_code == 400
    assert servers.chats == []


@pytest.mark.parametrize("body", [
    "[" * 100_000 + "]" * 100_000,
    json.dumps({"model": "x/y", "messages": [{"role": "user", "content": "hi"}], "deep": NESTED_TOO_DEEP}),
])
def test_deeply_nested_json_is_a_bad_request(app, admin, friend, servers, body):
    secret = make_key(admin, daily_limit=1)["key"]
    refused = outside(app).post("/v1/chat/completions", headers={**bearer(secret), "Content-Type": "application/json"},
                                content=body)
    assert refused.status_code == 400
    assert admin.get("/api/app-keys").json()["keys"][0]["last_used"] is None


@pytest.mark.parametrize("tool_calls", [5, "call", [1], [{"function": "read_file"}], [{"function": {"arguments": "{}"}}]])
def test_malformed_tool_calls_are_a_bad_request(app, admin, friend, servers, tool_calls):
    secret = make_key(admin, daily_limit=1)["key"]
    for stream in (False, True):
        refused = outside(app).post(
            "/v1/chat/completions", headers=bearer(secret),
            json={"model": model_id("friend-model"), "stream": stream,
                  "messages": [{"role": "assistant", "content": None, "tool_calls": tool_calls}]})
        assert refused.status_code == 400
    assert servers.chats == []
    assert admin.get("/api/app-keys").json()["keys"][0]["last_used"] is None


def test_an_oversized_request_is_refused_before_it_is_read(app, admin, friend, servers):
    secret = make_key(admin)["key"]
    big = "x" * (17 * 1024 * 1024)
    refused = ask(app, secret, extra_text=big)
    assert refused.status_code == 413
    assert servers.chats == []


def test_the_key_and_the_prompt_are_never_logged(app, admin, friend, caplog):
    caplog.set_level(logging.DEBUG)
    secret = make_key(admin, name="ArtyCode laptop")["key"]
    ask(app, secret)
    ask(app, secret, stream=True)
    outside(app).get("/v1/models", headers=bearer(secret[:-1] + "Z"))
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "ArtyCode laptop" in logged and "friend-model" in logged
    assert secret not in logged and secret[3:] not in logged
    assert PROMPT not in logged and FRIEND_KEY not in logged


def test_an_outside_openai_compatible_server_is_stored_with_its_key(admin, friend, tmp_path):
    stored = routes.endpoints.get(friend["id"])
    assert stored.base_url == FRIEND_URL and stored.provider == "openai" and stored.kind == "api-key"
    assert routes.api_keys.load()[friend["id"]] == FRIEND_KEY
    assert FRIEND_KEY not in (tmp_path / "api_keys.json").read_text()
    assert "friend-model" in [m["id"] for m in admin.get("/api/models").json()["models"]]


def test_a_backup_leaves_out_the_keys_and_a_restore_never_brings_one_back(app, admin, friend, tmp_path):
    made = make_key(admin)
    archive = admin.get("/api/backup").content
    assert "app_keys.json" not in zipfile.ZipFile(io.BytesIO(archive)).namelist()
    planted = io.BytesIO()
    with zipfile.ZipFile(planted, "w") as bundle:
        bundle.writestr("app_keys.json", (tmp_path / "app_keys.json").read_text())
    assert admin.delete(f"/api/app-keys/{made['id']}").status_code == 200
    for restored in (archive, planted.getvalue()):
        answer = admin.post("/api/restore", files={"file": ("backup.zip", restored, "application/zip")})
        assert answer.status_code == 200, answer.text
        assert ask(app, made["key"]).status_code == 401
    assert admin.get("/api/app-keys").json()["keys"] == []


def check_connections():
    asyncio.run(routes.connection_health.check_all())


def listed_models(app, secret):
    return outside(app).get("/v1/models", headers=bearer(secret)).json()["data"]


@pytest.fixture
def other(admin, servers):
    added = admin.post("/api/model-endpoints", json={"base_url": OTHER_URL, "api_key": "other-key", "kind": "api-key",
                                                     "label": "Local"})
    assert added.status_code == 200, added.text
    return added.json()


def test_v1_models_says_each_model_is_available(app, admin, friend):
    secret = make_key(admin)["key"]
    assert [m["available"] for m in listed_models(app, secret)] == [True, True]


def test_a_down_connection_keeps_its_models_listed_as_unavailable(app, admin, friend, servers):
    secret = make_key(admin)["key"]
    assert all(model["available"] for model in listed_models(app, secret))
    servers.friend_down = "refused"
    check_connections()
    assert [(m["id"], m["available"]) for m in listed_models(app, secret)] == [(model_id("friend-model"), False),
                                                                               (model_id("other-model"), False)]


def test_a_model_on_a_down_connection_is_refused_at_once(app, admin, friend, servers):
    secret = make_key(admin, daily_limit=5)["key"]
    listed_models(app, secret)
    servers.friend_down = "timeout"
    check_connections()
    refused = ask(app, secret)
    assert refused.status_code == 503 and refused.json()["error"]["code"] == "model_unavailable"
    assert int(refused.headers["retry-after"]) > 0
    assert servers.chats == []
    assert admin.get("/api/app-keys").json()["keys"][0]["last_used"] is None


def test_the_same_name_on_two_connections_never_falls_over(app, admin, friend, other, servers):
    secret = make_key(admin)["key"]
    ids = [m["id"] for m in listed_models(app, secret)]
    assert ids == [f"{friend['id']}/friend-model", f"{friend['id']}/other-model",
                   f"{other['id']}/friend-model", f"{other['id']}/other-model"]
    servers.friend_down = "refused"
    check_connections()
    refused = ask(app, secret, model=f"{friend['id']}/friend-model")
    assert refused.status_code == 503
    assert servers.chats == []
    assert ask(app, secret, model=f"{other['id']}/friend-model").status_code == 200
    assert [chat["url"] for chat in servers.chats] == [OTHER_URL + "/chat/completions"]


def test_a_key_for_one_connection_never_reaches_another(app, admin, friend, other, servers):
    secret = make_key(admin, grants=[{"endpoint_id": other["id"], "model": "*"}])["key"]
    assert {m["id"].split("/")[0] for m in listed_models(app, secret)} == {other["id"]}
    servers.friend_down = "refused"
    check_connections()
    assert ask(app, secret, model=f"{friend['id']}/friend-model").status_code == 403
    assert servers.chats == []


def test_a_connection_that_answers_again_is_available_again(app, admin, friend, servers):
    secret = make_key(admin)["key"]
    check_connections()
    servers.friend_down = "500"
    check_connections()
    assert ask(app, secret).status_code == 503
    servers.friend_down = None
    check_connections()
    assert ask(app, secret).status_code == 200
    assert all(model["available"] for model in listed_models(app, secret))


def test_a_connection_never_seen_up_offers_no_models(app, admin, friend, servers):
    secret = make_key(admin)["key"]
    servers.friend_down = "refused"
    check_connections()
    assert listed_models(app, secret) == []
    assert ask(app, secret).status_code == 503


@pytest.mark.parametrize("failure, kind", [("refused", "unreachable"), ("timeout", "timeout"),
                                           ("401", "unauthorized"), ("403", "unauthorized"),
                                           ("502", "server_error"), ("404", "http_error")])
def test_each_failure_is_named_without_secrets(app, admin, friend, servers, failure, kind):
    servers.friend_down = failure
    check_connections()
    health = next(e for e in admin.get("/api/model-endpoints").json()["endpoints"] if e["id"] == friend["id"])["health"]
    assert health["available"] is False and health["error_kind"] == kind
    assert set(health) == {"available", "last_checked", "last_change", "error_kind"}
    assert FRIEND_KEY not in json.dumps(health)


def test_last_change_moves_only_when_the_connection_flips(app, admin, friend, servers, clock):
    check_connections()
    first = routes.connection_health.state(friend["id"])
    clock.now += 60
    check_connections()
    steady = routes.connection_health.state(friend["id"])
    assert steady.last_checked == clock.now and steady.last_change == first.last_change
    clock.now += 60
    servers.friend_down = "refused"
    check_connections()
    flipped = routes.connection_health.state(friend["id"])
    assert flipped.available is False and flipped.last_change == clock.now


def test_chat_requests_use_the_checked_model_list(app, admin, friend, servers):
    secret = make_key(admin)["key"]
    ask(app, secret)
    lists_before = servers.model_lists
    for _ in range(3):
        assert ask(app, secret).status_code == 200
    assert servers.model_lists == lists_before


def test_a_removed_connection_is_forgotten(app, admin, friend):
    check_connections()
    assert admin.delete(f"/api/model-endpoints/{friend['id']}").status_code == 200
    check_connections()
    assert routes.connection_health.state(friend["id"]) is None


def test_the_checker_stops_when_told(servers):
    async def run_and_stop():
        checker = ConnectionHealth(lambda: [], lambda e: None)
        running = asyncio.create_task(checker.run_loop())
        await asyncio.sleep(0)
        checker.stop()
        await asyncio.wait_for(running, timeout=2)

    asyncio.run(run_and_stop())
