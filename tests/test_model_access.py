import asyncio
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
import model_access
import chat_stream
import routes
import user_data
from api_key_manager import APIKeyManager
from auth import SessionStore, UserStore
from config import config
from generation_quota import GenerationQuota
from model_access import ModelAccessStore
from model_endpoints import ModelEndpointStore

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}
LISTED = ["gpt-4o", "example-9b-abl", "Example-8B-ABL", "qwen3-abl-q4", "example-abliterated:14b",
          "own/example-uncensored", "uncensored-2", "llama3.1:8b"]
RESTRICTED = {"example-9b-abl", "Example-8B-ABL", "qwen3-abl-q4", "example-abliterated:14b", "own/example-uncensored",
              "uncensored-2"}


@pytest.fixture
def app(tmp_path, monkeypatch):
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
    hashed = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    users.upsert("admin@example.com", "Admin", "admin", hashed)
    users.upsert("sam@example.com", "Sam", "user", hashed)
    application = FastAPI()
    auth.install(application, users, sessions)
    application.include_router(auth_routes.router)
    application.include_router(routes.router)
    return application


@pytest.fixture
def endpoint(app):
    return routes.endpoints.add("https://models.example.com/v1", kind="api-key")


@pytest.fixture
def model_server(monkeypatch):
    real_client = httpx.AsyncClient
    seen = []

    def answer(request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": name} for name in LISTED]})

    def client(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(answer), **kwargs)

    monkeypatch.setattr(routes.httpx, "AsyncClient", client)
    return seen


@pytest.fixture
def replies(monkeypatch):
    asked = []

    async def fake_stream(base_url, api_key, model, messages, system, options):
        asked.append(model)
        yield chat_stream._sse_line({"delta": "hi"})
        yield chat_stream._sse_line({"done": True})

    monkeypatch.setattr(routes, "stream_chat", fake_stream)
    return asked


def login(app, email):
    client = TestClient(app, base_url="https://testserver", headers=HEADERS)
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    client.user_id = auth_routes.users.find_by_email(email).id
    return client


def chat(client, endpoint_id, model):
    return client.post("/api/chat_stream", json={"endpoint_id": endpoint_id, "model": model,
                                                 "messages": [{"role": "user", "content": "hello"}]})


def listed(client):
    return [m["id"] for m in client.get("/api/models").json()["models"]]


def test_an_ordinary_account_never_sees_the_restricted_models(app, endpoint, model_server):
    sam, admin = login(app, "sam@example.com"), login(app, "admin@example.com")
    assert listed(sam) == [name for name in LISTED if name not in RESTRICTED]
    assert listed(admin) == LISTED


@pytest.mark.parametrize("model", sorted(RESTRICTED))
def test_an_ordinary_account_cannot_chat_with_a_restricted_model(app, endpoint, replies, model):
    sam = login(app, "sam@example.com")
    refused = chat(sam, endpoint.id, model)
    assert refused.status_code == 403 and refused.json()["detail"] == f"this account is not allowed to use the model {model}"
    assert replies == []


def test_an_admin_can_chat_with_any_model(app, endpoint, replies):
    admin = login(app, "admin@example.com")
    assert chat(admin, endpoint.id, "own/example-uncensored").status_code == 200 and replies == ["own/example-uncensored"]


def test_every_other_route_that_takes_a_model_refuses_it_too(app, endpoint, monkeypatch):
    sam = login(app, "sam@example.com")
    warmed = []
    monkeypatch.setattr(routes, "_warm_brain_model", lambda *args: warmed.append(args))
    model = "example-8b-abl"
    assert sam.post("/api/quick-complete", json={"endpoint_id": endpoint.id, "model": model,
                                                 "prompt": "x"}).status_code == 403
    assert sam.post("/api/tasks", json={"prompt": "x", "schedule": "daily", "scheduled_time": "08:00",
                                        "endpoint_id": endpoint.id, "model": model}).status_code == 403
    assert sam.post("/api/email/rules", json={"account_id": "a", "match_value": "x", "action": "ai_summarize_note",
                                              "endpoint_id": endpoint.id, "model": model}).status_code == 403
    assert sam.post("/api/speak", json={"model": model, "text": "hi"}).status_code == 403
    assert sam.post("/api/warm", json={"endpoint_id": endpoint.id, "model": model}).json() == {"ok": True}
    assert sam.get("/api/tasks").json()["tasks"] == [] and warmed == []


def test_a_scheduled_run_with_a_restricted_model_is_refused_when_it_runs(app, endpoint):
    sam = login(app, "sam@example.com")
    with user_data.run_as(sam.user_id), pytest.raises(RuntimeError, match="not allowed to use the model own/example-uncensored"):
        asyncio.run(routes._task_chat_fn(endpoint.id, "own/example-uncensored", "x", [], []))
    with user_data.run_as(sam.user_id), pytest.raises(RuntimeError, match="not allowed to use the model own/example-uncensored"):
        asyncio.run(routes._email_rule_chat_fn(endpoint.id, "own/example-uncensored", "x"))


def test_an_admin_decides_which_models_an_account_may_use(app, endpoint, model_server, replies):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    saved = admin.put(f"/api/auth/users/{sam.user_id}/models", json={"models": ["own/example-uncensored", " llama* ", ""]})
    assert saved.json()["models"] == ["own/example-uncensored", "llama*"]
    assert listed(sam) == ["own/example-uncensored", "llama3.1:8b"]
    assert chat(sam, endpoint.id, "gpt-4o").status_code == 403
    assert chat(sam, endpoint.id, "own/example-uncensored").status_code == 200
    assert admin.put(f"/api/auth/users/{sam.user_id}/models", json={"models": None}).json()["models"] is None
    assert listed(sam) == [name for name in LISTED if name not in RESTRICTED]


def test_only_an_admin_can_read_or_change_model_access(app):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    assert sam.get(f"/api/auth/users/{sam.user_id}/models").status_code == 403
    assert sam.put(f"/api/auth/users/{sam.user_id}/models", json={"models": ["*"]}).status_code == 403
    assert admin.get("/api/auth/users/" + "0" * 32 + "/models").status_code == 404
    assert admin.put(f"/api/auth/users/{sam.user_id}/models", json={"models": ["x" * 300]}).status_code == 400
    assert admin.get(f"/api/auth/users/{sam.user_id}/models").json()["models"] is None


def test_the_restricted_patterns_come_from_the_environment(app, endpoint, replies, monkeypatch):
    monkeypatch.setattr(config, "restricted_models", "gpt-*")
    sam = login(app, "sam@example.com")
    assert chat(sam, endpoint.id, "GPT-4o").status_code == 403
    assert chat(sam, endpoint.id, "own/example-uncensored").status_code == 200


def test_too_many_replies_a_minute_get_a_clear_429_and_admins_are_not_limited(app, endpoint, replies, monkeypatch):
    monkeypatch.setattr(config, "generations_per_minute", 2)
    sam, admin = login(app, "sam@example.com"), login(app, "admin@example.com")
    assert [chat(sam, endpoint.id, "gpt-4o").status_code for _ in range(2)] == [200, 200]
    limited = chat(sam, endpoint.id, "gpt-4o")
    assert limited.status_code == 429 and 1 <= int(limited.headers["retry-after"]) <= 60
    assert limited.json()["detail"].startswith("this account can ask for 2 model replies a minute")
    limited_helper = sam.post("/api/quick-complete", json={"endpoint_id": endpoint.id, "model": "gpt-4o", "prompt": "x"})
    assert limited_helper.status_code == 429
    assert [chat(admin, endpoint.id, "gpt-4o").status_code for _ in range(3)] == [200, 200, 200]
    assert len(replies) == 5


def test_a_refused_model_does_not_use_up_the_quota(app, endpoint, replies, monkeypatch):
    monkeypatch.setattr(config, "generations_per_minute", 1)
    sam = login(app, "sam@example.com")
    assert chat(sam, endpoint.id, "own/example-uncensored").status_code == 403
    assert chat(sam, endpoint.id, "gpt-4o").status_code == 200


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def test_the_minute_window_slides_and_the_day_starts_again_at_midnight_utc():
    clock = Clock(1_800_000_000.0)
    quota = GenerationQuota(clock)
    assert quota.take("a", 2, 3) is None and quota.take("a", 2, 3) is None
    assert quota.take("a", 2, 3).retry_after_seconds == 60
    clock.now += 61
    assert quota.take("a", 2, 3) is None
    refused = quota.take("a", 2, 3)
    assert refused.message.startswith("this account has used its 3 model replies for today")
    clock.now += refused.retry_after_seconds
    assert quota.take("a", 2, 3) is None
    assert quota.take("b", 2, 3) is None


def test_zero_turns_a_limit_off_and_scheduled_runs_only_count_toward_the_day():
    quota = GenerationQuota(Clock(1_800_000_000.0))
    assert all(quota.take("a", 0, 0) is None for _ in range(100))
    assert all(quota.take("b", 1, 5, counts_per_minute=False) is None for _ in range(5))
    assert quota.take("b", 1, 5, counts_per_minute=False) is not None


@pytest.mark.parametrize("model", ["example-abl:latest", "library/example-9b-abl", "some-abliterated:14b",
                                   "own/example-uncensored", "Uncensored-2:latest"])
def test_the_default_restricted_patterns_are_generic_and_cover_tagged_names(model):
    defaults = type(config)().restricted_model_patterns()
    assert model_access.matches_any(model, defaults)
    assert not any(model_access.matches_any(name, defaults) for name in ("gpt-4o", "llama3.1:8b", "qwen3:14b"))


def test_running_a_task_now_counts_toward_the_minute_limit(app, endpoint, replies, monkeypatch):
    monkeypatch.setattr(config, "generations_per_minute", 2)
    started = []

    async def run_now(task):
        started.append(task.id)

    monkeypatch.setattr(routes.scheduler, "run_now", run_now)
    sam = login(app, "sam@example.com")
    task_id = sam.post("/api/tasks", json={"prompt": "x", "schedule": "daily", "scheduled_time": "08:00",
                                           "endpoint_id": endpoint.id, "model": "gpt-4o"}).json()["id"]
    assert sam.post(f"/api/tasks/{task_id}/run").status_code == 200
    assert chat(sam, endpoint.id, "gpt-4o").status_code == 200
    limited = sam.post(f"/api/tasks/{task_id}/run")
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
    assert started == [task_id]


def test_running_a_task_now_is_counted_toward_the_day_only_once_by_the_run_itself():
    quota = GenerationQuota(Clock(1_800_000_000.0))
    assert quota.take("a", 5, 1, counts_per_day=False) is None
    assert quota.take("a", 5, 1, counts_per_minute=False) is None
    assert quota.take("a", 5, 1) is not None
