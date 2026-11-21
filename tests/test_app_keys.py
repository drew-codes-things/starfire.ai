import json
import logging
import os
import sys

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
from generation_quota import GenerationQuota
from model_access import ModelAccessStore
from model_endpoints import ModelEndpointStore

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}
FRIEND_URL = "https://friend.drew-gnr.xyz/v1"
FRIEND_KEY = "llama-server-key-for-tests"
LISTED = ["friend-model", "other-model", "example-9b-abl"]
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

    def answer(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
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
    monkeypatch.setattr(app_key_api, "app_keys", AppKeyStore(data_dir, clock=clock))
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


def make_key(admin, email="sam@example.com", **details):
    made = admin.post("/api/app-keys", json={"name": details.pop("name", "Arty phone"), "user_id": user_id(email),
                                             **details})
    assert made.status_code == 200, made.text
    return made.json()


def outside(app, address=("testclient", 50000), headers=None):
    return TestClient(app, base_url="https://testserver", client=address, headers=headers or {})


def bearer(secret):
    return {"Authorization": f"Bearer {secret}"}


def ask(app, secret, model="friend-model", stream=False, **extra):
    return outside(app).post("/v1/chat/completions", headers=bearer(secret),
                             json={"model": model, "stream": stream, "messages": [{"role": "user", "content": PROMPT}],
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


def test_the_made_key_answer_is_never_cached(admin, friend):
    made = admin.post("/api/app-keys", json={"name": "ArtyCode", "user_id": user_id("sam@example.com")})
    assert made.headers["cache-control"] == "no-store"


def test_two_keys_are_never_the_same(admin, friend):
    assert make_key(admin)["key"] != make_key(admin)["key"]


def test_only_an_admin_manages_app_keys(app, admin, friend):
    sam = login(app, "sam@example.com")
    assert sam.get("/api/app-keys").status_code == 403
    assert sam.post("/api/app-keys", json={"name": "mine", "user_id": sam.user_id}).status_code == 403
    secret_id = make_key(admin)["id"]
    assert sam.delete(f"/api/app-keys/{secret_id}").status_code == 403


def test_a_key_lists_only_the_models_its_account_may_use(app, admin, friend):
    secret = make_key(admin)["key"]
    listed = outside(app).get("/v1/models", headers=bearer(secret))
    assert listed.status_code == 200
    assert [m["id"] for m in listed.json()["data"]] == ["friend-model", "other-model"]


def test_a_key_with_a_model_list_lists_only_those(app, admin, friend):
    secret = make_key(admin, models=["friend-model"])["key"]
    assert [m["id"] for m in outside(app).get("/v1/models", headers=bearer(secret)).json()["data"]] == ["friend-model"]


def test_a_model_outside_the_key_list_is_refused(app, admin, friend, servers):
    secret = make_key(admin, models=["friend-model"])["key"]
    refused = ask(app, secret, model="other-model")
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "model_not_allowed"
    assert servers.chats == []


def test_a_restricted_model_is_refused_to_an_ordinary_account_key(app, admin, friend, servers):
    secret = make_key(admin)["key"]
    refused = ask(app, secret, model="example-9b-abl")
    assert refused.status_code == 403
    assert servers.chats == []


def test_a_key_for_an_ordinary_account_cannot_be_given_a_restricted_model(admin, friend):
    refused = admin.post("/api/app-keys", json={"name": "x", "user_id": user_id("sam@example.com"),
                                                "models": ["example-9b-abl"]})
    assert refused.status_code == 400 and "example-9b-abl" in refused.json()["detail"]


def test_an_admin_account_key_may_use_a_restricted_model(app, admin, friend, servers):
    secret = make_key(admin, email="admin@example.com")["key"]
    assert ask(app, secret, model="example-9b-abl").status_code == 200


def test_a_wrong_or_missing_key_is_refused(app, friend, servers):
    for headers in ({}, bearer("sf_" + "A" * 43), bearer(PASSWORD), {"Authorization": "Basic abc"}):
        refused = outside(app).get("/v1/models", headers=headers)
        assert refused.status_code == 401 and refused.json()["error"]["code"] == "invalid_api_key"
    assert servers.chats == []


def test_a_signed_in_session_cookie_does_not_open_the_api(app, admin, friend, servers):
    refused = admin.post("/v1/chat/completions", json={"model": "friend-model",
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


def test_a_disabled_account_key_is_refused(app, admin, friend):
    secret = make_key(admin)["key"]
    assert admin.patch(f"/api/auth/users/{user_id('sam@example.com')}", json={"disabled": True}).status_code == 200
    assert ask(app, secret).status_code == 401


def test_a_key_daily_limit_answers_429(app, admin, friend, servers):
    secret = make_key(admin, daily_limit=2)["key"]
    assert [ask(app, secret).status_code for _ in range(2)] == [200, 200]
    refused = ask(app, secret)
    assert refused.status_code == 429 and int(refused.headers["retry-after"]) > 0
    assert len(servers.chats) == 2


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


def test_last_used_is_shown_after_a_reply(app, admin, friend, clock):
    secret = make_key(admin)["key"]
    assert admin.get("/api/app-keys").json()["keys"][0]["last_used"] is None
    ask(app, secret)
    assert admin.get("/api/app-keys").json()["keys"][0]["last_used"] == clock.now


def test_wrong_keys_lock_out_the_address_even_with_a_right_key(app, admin, friend):
    secret = make_key(admin)["key"]
    for _ in range(app_key_api.BAD_KEY_MAX_FAILURES):
        assert outside(app).get("/v1/models", headers=bearer("sf_" + "B" * 43)).status_code == 401
    locked = outside(app).get("/v1/models", headers=bearer(secret))
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0


def test_the_lockout_follows_the_visitor_address_behind_a_trusted_proxy(app, admin, friend):
    secret = make_key(admin)["key"]
    proxy = ("127.0.0.1", 40000)
    for _ in range(app_key_api.BAD_KEY_MAX_FAILURES):
        outside(app, proxy, {"CF-Connecting-IP": "203.0.113.5"}).get("/v1/models", headers=bearer("sf_" + "C" * 43))
    assert outside(app, proxy, {"CF-Connecting-IP": "203.0.113.5"}).get(
        "/v1/models", headers=bearer(secret)).status_code == 429
    assert outside(app, proxy, {"CF-Connecting-IP": "203.0.113.6"}).get(
        "/v1/models", headers=bearer(secret)).status_code == 200


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
                                 json={"model": "friend-model", "messages": history, "tools": TOOLS})
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


def test_a_refused_upstream_key_is_not_reported_as_a_bad_app_key(app, admin, friend, servers, monkeypatch):
    secret = make_key(admin)["key"]
    monkeypatch.setattr(servers, "answer", lambda request: httpx.Response(
        200, json={"data": [{"id": "friend-model"}]}) if request.url.path.endswith("/models") else httpx.Response(401))
    refused = ask(app, secret)
    assert refused.status_code == 502


def test_more_than_one_reply_per_request_is_refused(app, admin, friend, servers):
    secret = make_key(admin)["key"]
    assert ask(app, secret, n=3).status_code == 400
    assert servers.chats == []


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
