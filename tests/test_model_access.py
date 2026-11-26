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
import remote_rules
import routes
import user_data
from api_key_manager import APIKeyManager
from auth import SessionStore, UserStore
from config import config
from generation_quota import GenerationQuota
from connection_health import ConnectionHealth
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


def test_an_admin_can_chat_with_any_model(app, endpoint, replies, unreadable_model_lists):
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


def test_the_restricted_patterns_come_from_the_environment(app, endpoint, replies, monkeypatch,
                                                          unreadable_model_lists):
    monkeypatch.setattr(config, "restricted_models", "gpt-*")
    sam = login(app, "sam@example.com")
    assert chat(sam, endpoint.id, "GPT-4o").status_code == 403
    assert chat(sam, endpoint.id, "own/example-uncensored").status_code == 200


def test_too_many_replies_a_minute_get_a_clear_429_and_admins_are_not_limited(app, endpoint, model_server, replies,
                                                                              monkeypatch):
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


def test_a_refused_model_does_not_use_up_the_quota(app, endpoint, replies, monkeypatch, unreadable_model_lists):
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


def test_running_a_task_now_counts_toward_the_minute_limit(app, endpoint, model_server, replies, monkeypatch):
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


def access_url(user_id):
    return f"/api/auth/users/{user_id}/models"


def available(answer):
    return {m["id"]: m for m in answer["available"]}


def test_the_admin_sees_every_model_starfire_knows_grouped_by_connection(app, endpoint, model_server):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    answer = admin.get(access_url(sam.user_id)).json()
    assert [m["id"] for m in answer["available"]] == LISTED
    assert {m["connection"] for m in answer["available"]} == {"models.example.com"}
    assert {m["endpoint_id"] for m in answer["available"]} == {endpoint.id}
    assert {name for name, m in available(answer).items() if m["restricted"]} == RESTRICTED
    assert {name for name, m in available(answer).items() if m["allowed"]} == set(LISTED) - RESTRICTED


def test_the_admin_saves_a_checklist_and_it_takes_effect_at_once(app, endpoint, model_server, replies):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    saved = admin.put(access_url(sam.user_id), json={"models": ["gpt-4o", "own/example-uncensored"]}).json()
    assert saved["models"] == ["gpt-4o", "own/example-uncensored"]
    assert {name for name, m in available(saved).items() if m["allowed"]} == {"gpt-4o", "own/example-uncensored"}
    assert not any(m["by_pattern"] for m in saved["available"])
    assert sorted(listed(sam)) == ["gpt-4o", "own/example-uncensored"]
    assert chat(sam, endpoint.id, "llama3.1:8b").status_code == 403
    assert chat(sam, endpoint.id, "gpt-4o").status_code == 200


def test_all_models_means_every_unrestricted_model(app, endpoint, model_server):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    admin.put(access_url(sam.user_id), json={"models": ["gpt-4o"]})
    saved = admin.put(access_url(sam.user_id), json={"models": None}).json()
    assert saved["models"] is None
    assert listed(sam) == [name for name in LISTED if name not in RESTRICTED]


def test_an_empty_checklist_gives_the_account_no_models(app, endpoint, model_server):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    assert admin.put(access_url(sam.user_id), json={"models": []}).json()["models"] == []
    assert listed(sam) == []


def test_a_saved_name_that_no_longer_exists_is_kept_and_reported(app, endpoint, model_server):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    saved = admin.put(access_url(sam.user_id), json={"models": ["gpt-4o", "retired-model:7b", "llama*"]}).json()
    assert saved["models"] == ["gpt-4o", "retired-model:7b", "llama*"]
    assert saved["kept"] == ["retired-model:7b", "llama*"]
    assert listed(sam) == ["gpt-4o", "llama3.1:8b"]
    assert available(saved)["llama3.1:8b"]["by_pattern"] is True
    assert available(saved)["gpt-4o"]["by_pattern"] is False
    again = admin.get(access_url(sam.user_id)).json()
    assert again["models"] == saved["models"]


def test_malformed_lists_are_refused_and_change_nothing(app, endpoint, model_server):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    admin.put(access_url(sam.user_id), json={"models": ["gpt-4o"]})
    assert admin.put(access_url(sam.user_id), json={"models": "gpt-4o"}).status_code == 422
    assert admin.put(access_url(sam.user_id), json={"models": [f"m{i}" for i in range(1001)]}).status_code == 400
    assert admin.put(access_url(sam.user_id), json={"models": ["x" * 201]}).status_code == 400
    assert admin.get(access_url(sam.user_id)).json()["models"] == ["gpt-4o"]


def test_an_ordinary_account_cannot_read_or_set_anyones_access(app, endpoint, model_server):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    assert sam.get(access_url(admin.user_id)).status_code == 403
    assert sam.put(access_url(sam.user_id), json={"models": None}).status_code == 403
    assert sam.get("/api/auth/new-account-models").status_code == 403
    assert sam.put("/api/auth/new-account-models", json={"models": ["gpt-4o"]}).status_code == 403
    assert admin.get("/api/auth/new-account-models").json()["models"] is None


def test_nobody_signed_out_can_reach_the_access_endpoints(app):
    stranger = TestClient(app, base_url="https://testserver", headers=HEADERS)
    assert stranger.get("/api/auth/new-account-models").status_code in (401, 403)
    assert stranger.put("/api/auth/new-account-models", json={"models": None}).status_code in (401, 403)
    assert stranger.get(access_url("0" * 32)).status_code in (401, 403)


def test_a_disabled_admin_loses_the_right_to_set_access(app, endpoint, model_server):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    auth_routes.users.update(sam.user_id, role="admin")
    auth_routes.users.update(admin.user_id, disabled=True)
    assert admin.put(access_url(sam.user_id), json={"models": None}).status_code in (401, 403)


def test_the_default_for_new_accounts_is_applied_to_accounts_made_afterwards(app, endpoint, model_server):
    admin = login(app, "admin@example.com")
    saved = admin.put("/api/auth/new-account-models", json={"models": ["gpt-4o"]}).json()
    assert saved["models"] == ["gpt-4o"]
    made = admin.post("/api/auth/users", json={"email": "kim@example.com", "name": "Kim", "password": PASSWORD})
    assert made.status_code == 200
    assert admin.get(access_url(made.json()["id"])).json()["models"] == ["gpt-4o"]
    kim = login(app, "kim@example.com")
    assert listed(kim) == ["gpt-4o"]
    assert listed(login(app, "sam@example.com")) == [name for name in LISTED if name not in RESTRICTED]


def test_changing_the_default_leaves_existing_accounts_alone(app, endpoint, model_server):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    admin.put("/api/auth/new-account-models", json={"models": ["gpt-4o"]})
    assert admin.get(access_url(sam.user_id)).json()["models"] is None


def test_with_no_default_a_new_account_gets_every_unrestricted_model(app, endpoint, model_server):
    admin = login(app, "admin@example.com")
    admin.put("/api/auth/new-account-models", json={"models": ["gpt-4o"]})
    admin.put("/api/auth/new-account-models", json={"models": None})
    made = admin.post("/api/auth/users", json={"email": "kim@example.com", "name": "Kim", "password": PASSWORD})
    assert admin.get(access_url(made.json()["id"])).json()["models"] is None


def test_the_default_cannot_be_mistaken_for_an_account(app):
    admin = login(app, "admin@example.com")
    admin.put("/api/auth/new-account-models", json={"models": ["gpt-4o"]})
    assert routes.model_access_store.allowed_for("sam") is None
    assert admin.get(access_url("new-accounts")).status_code == 404


@pytest.fixture
def switchable_server(monkeypatch):
    real_client = httpx.AsyncClient
    state = {"up": True}

    def answer(request):
        if not state["up"]:
            return httpx.Response(530, text="origin is unreachable")
        return httpx.Response(200, json={"data": [{"id": name} for name in LISTED]})

    def client(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(answer), **kwargs)

    monkeypatch.setattr(routes.httpx, "AsyncClient", client)
    return state


def check_connection(endpoint):
    asyncio.run(routes.connection_health.check(endpoint))


def models_of(client):
    return {m["id"]: m for m in client.get("/api/models").json()["models"]}


def test_a_down_connection_keeps_its_models_in_the_picker_marked_offline(app, endpoint, switchable_server):
    sam = login(app, "sam@example.com")
    check_connection(endpoint)
    assert not any(m["offline"] for m in models_of(sam).values())
    switchable_server["up"] = False
    check_connection(endpoint)
    shown = models_of(sam)
    assert set(shown) == set(LISTED) - RESTRICTED
    assert all(m["offline"] for m in shown.values())
    assert all(m["connection"] == "Connection 1" for m in shown.values())
    assert {m["connection"] for m in models_of(login(app, "admin@example.com")).values()} == {"models.example.com"}
    health = shown["gpt-4o"]["health"]
    assert health["available"] is False and health["error_kind"] == "server_error" and health["last_checked"] > 0


def test_auto_never_picks_a_model_on_a_down_connection(app, endpoint, switchable_server, replies):
    sam = login(app, "sam@example.com")
    check_connection(endpoint)
    assert sam.get("/api/models").json()["auto_everyday"] is not None
    switchable_server["up"] = False
    check_connection(endpoint)
    assert sam.get("/api/models").json()["auto_everyday"] is None
    refused = chat(sam, endpoint.id, "auto")
    assert refused.status_code == 503 and replies == []


def test_a_chat_to_a_down_model_is_refused_at_once_with_a_clear_error(app, endpoint, switchable_server, replies):
    sam = login(app, "sam@example.com")
    check_connection(endpoint)
    switchable_server["up"] = False
    check_connection(endpoint)
    refused = chat(sam, endpoint.id, "gpt-4o")
    assert refused.status_code == 503
    assert refused.json()["detail"].startswith("Connection 1 is offline (server error, last checked ")
    assert replies == []


def age_last_check(endpoint):
    routes.connection_health.state(endpoint.id).last_checked -= routes.FRESH_CHECK_SECONDS + 1


def test_a_refused_offline_chat_does_not_use_up_the_days_replies(app, endpoint, switchable_server, replies,
                                                                 monkeypatch):
    monkeypatch.setattr(config, "generations_per_day", 1)
    sam = login(app, "sam@example.com")
    check_connection(endpoint)
    switchable_server["up"] = False
    check_connection(endpoint)
    assert chat(sam, endpoint.id, "gpt-4o").status_code == 503
    switchable_server["up"] = True
    age_last_check(endpoint)
    assert chat(sam, endpoint.id, "gpt-4o").status_code == 200
    assert chat(sam, endpoint.id, "gpt-4o").status_code == 429


def test_offline_chats_still_count_toward_the_minute_limit(app, endpoint, switchable_server, replies, monkeypatch):
    monkeypatch.setattr(config, "generations_per_minute", 2)
    sam = login(app, "sam@example.com")
    check_connection(endpoint)
    switchable_server["up"] = False
    check_connection(endpoint)
    assert [chat(sam, endpoint.id, "gpt-4o").status_code for _ in range(3)] == [503, 503, 429]


def test_a_fresh_offline_check_is_trusted_and_an_old_one_is_redone(app, endpoint, monkeypatch, replies):
    sam = login(app, "sam@example.com")
    asked = []

    def answer(request):
        asked.append(request.url.path)
        return httpx.Response(530, text="origin is unreachable")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(routes.httpx, "AsyncClient",
                        lambda *args, **kwargs: real_client(*args, transport=httpx.MockTransport(answer), **kwargs))
    check_connection(endpoint)
    asked.clear()
    assert chat(sam, endpoint.id, "gpt-4o").status_code == 503
    assert asked == []
    age_last_check(endpoint)
    assert chat(sam, endpoint.id, "gpt-4o").status_code == 503
    assert len(asked) == 1


def test_the_offline_recheck_waits_until_the_quota_allows_a_reply(app, endpoint, monkeypatch, replies):
    monkeypatch.setattr(config, "generations_per_minute", 1)
    sam = login(app, "sam@example.com")
    rechecked = []

    async def recheck(checked):
        rechecked.append(checked.id)

    check_connection_down(endpoint, monkeypatch)
    age_last_check(endpoint)
    monkeypatch.setattr(routes.connection_health, "check", recheck)
    assert chat(sam, endpoint.id, "gpt-4o").status_code == 503
    assert chat(sam, endpoint.id, "gpt-4o").status_code == 429
    assert rechecked == [endpoint.id]


def check_connection_down(endpoint, monkeypatch):
    real_client = httpx.AsyncClient
    with monkeypatch.context() as patched:
        patched.setattr(routes.httpx, "AsyncClient", lambda *args, **kwargs: real_client(
            *args, transport=httpx.MockTransport(lambda request: httpx.Response(530)), **kwargs))
        check_connection(endpoint)


def test_a_connection_with_an_unreadable_list_still_takes_chats(app, endpoint, monkeypatch, replies):
    sam = login(app, "sam@example.com")
    real_client = httpx.AsyncClient
    monkeypatch.setattr(routes.httpx, "AsyncClient", lambda *args, **kwargs: real_client(
        *args, transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": 5})), **kwargs))
    check_connection(endpoint)
    assert routes.connection_health.state(endpoint.id).error_kind == "bad_answer"
    assert chat(sam, endpoint.id, "gpt-4o").status_code == 200


def test_a_connection_that_comes_back_is_usable_again_without_waiting_for_the_next_check(app, endpoint, switchable_server, replies):
    sam = login(app, "sam@example.com")
    check_connection(endpoint)
    switchable_server["up"] = False
    check_connection(endpoint)
    switchable_server["up"] = True
    age_last_check(endpoint)
    assert chat(sam, endpoint.id, "gpt-4o").status_code == 200
    assert not any(m["offline"] for m in models_of(sam).values())


def test_offline_models_follow_the_same_access_rules(app, endpoint, switchable_server):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    check_connection(endpoint)
    admin.put(access_url(sam.user_id), json={"models": ["gpt-4o"]})
    switchable_server["up"] = False
    check_connection(endpoint)
    assert list(models_of(sam)) == ["gpt-4o"]
    assert set(models_of(admin)) == set(LISTED)


def test_a_connection_never_checked_adds_no_phantom_models(app, endpoint, switchable_server):
    sam = login(app, "sam@example.com")
    switchable_server["up"] = False
    assert models_of(sam) == {}


def test_the_access_list_includes_the_last_known_models_of_a_down_connection(app, endpoint, switchable_server):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    check_connection(endpoint)
    switchable_server["up"] = False
    check_connection(endpoint)
    answer = admin.get(access_url(sam.user_id)).json()
    names = [m["id"] for m in answer["available"]]
    assert sorted(names) == sorted(LISTED)
    assert {name for name, m in available(answer).items() if m["restricted"]} == RESTRICTED
    assert len(names) == len(set(names))
    assert all(m["offline"] is True for m in answer["available"])
    assert {m["connection"] for m in answer["available"]} == {"models.example.com"}


def test_an_online_connection_adds_no_offline_entries_to_the_access_list(app, endpoint, switchable_server):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    check_connection(endpoint)
    answer = admin.get(access_url(sam.user_id)).json()
    assert [m["id"] for m in answer["available"]] == LISTED
    assert not any(m["offline"] for m in answer["available"])


def test_a_ticked_model_on_a_down_connection_is_saved_by_name_and_shows_ticked(app, endpoint, switchable_server):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    check_connection(endpoint)
    switchable_server["up"] = False
    check_connection(endpoint)
    saved = admin.put(access_url(sam.user_id), json={"models": ["gpt-4o"]}).json()
    assert saved["kept"] == []
    assert available(saved)["gpt-4o"]["allowed"] is True and available(saved)["gpt-4o"]["by_pattern"] is False
    assert available(saved)["llama3.1:8b"]["allowed"] is False


def restart_health(monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "connection_health", ConnectionHealth(
        lambda: routes.endpoints.list(), lambda e: None, data_dir=str(tmp_path)))


def test_a_down_connections_models_are_still_listed_after_a_restart(app, endpoint, switchable_server, monkeypatch, tmp_path):
    restart_health(monkeypatch, tmp_path)
    sam = login(app, "sam@example.com")
    check_connection(endpoint)
    switchable_server["up"] = False
    restart_health(monkeypatch, tmp_path)
    shown = models_of(sam)
    assert set(shown) == set(LISTED) - RESTRICTED
    assert all(m["offline"] for m in shown.values())
    assert shown["gpt-4o"]["health"]["available"] is False
    assert shown["gpt-4o"]["health"]["last_checked"] > 0
    answer = login(app, "admin@example.com").get(access_url(sam.user_id)).json()
    assert sorted(m["id"] for m in answer["available"]) == sorted(LISTED)


def test_a_deleted_connections_models_are_not_remembered(app, endpoint, switchable_server, monkeypatch, tmp_path):
    restart_health(monkeypatch, tmp_path)
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    check_connection(endpoint)
    assert admin.delete(f"/api/model-endpoints/{endpoint.id}").status_code == 200
    switchable_server["up"] = False
    restart_health(monkeypatch, tmp_path)
    assert models_of(sam) == {}
    asyncio.run(routes.connection_health.check_all())
    with open(tmp_path / "connection_models.json", encoding="utf-8") as f:
        assert endpoint.id not in f.read()


def test_the_remembered_lists_hold_no_keys_or_addresses(app, endpoint, switchable_server, monkeypatch, tmp_path):
    restart_health(monkeypatch, tmp_path)
    routes.api_keys.save(endpoint.id, "sk-secret-key-123")
    routes.connection_health.api_key_fn = routes._endpoint_key
    check_connection(endpoint)
    with open(tmp_path / "connection_models.json", encoding="utf-8") as f:
        text = f.read()
    assert "gpt-4o" in text
    assert "sk-secret-key-123" not in text
    assert endpoint.base_url not in text
    assert "api_key" not in text


@pytest.mark.parametrize("url,remote", [
    ("http://10.20.30.16:8080/v1", False), ("http://10.0.0.5/v1", False), ("http://172.20.1.1/v1", False),
    ("http://169.254.3.4/v1", False), ("http://[fd12:3456::1]/v1", False), ("http://[fe80::1]/v1", False),
    ("http://127.0.0.1:11434/v1", False), ("http://localhost:8080/v1", False), ("http://[::1]:8080/v1", False),
    ("https://friend.example.net/v1", True), ("https://8.8.8.8/v1", True), ("http://172.32.0.1/v1", True),
    ("https://quick-tunnel-example.trycloudflare.com/v1", True),
])
def test_a_connection_is_remote_when_its_host_is_not_on_the_home_network(app, url, remote):
    assert remote_rules.is_remote_connection(routes.endpoints.add(url, kind="api-key")) is remote


def test_a_ruled_connection_is_remote_even_on_a_home_address(app, monkeypatch):
    lan = routes.endpoints.add("http://10.20.30.16:8080/v1", kind="api-key")
    monkeypatch.setattr(config, "remote_rules_connections", lan.id)
    assert remote_rules.is_remote_connection(lan) is True


def test_every_model_says_whether_its_connection_is_remote_and_never_names_the_host(app, model_server):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    home = routes.endpoints.add("http://10.20.30.16:8080/v1", kind="api-key")
    far = routes.endpoints.add("https://friend.example.net/v1", kind="api-key")
    shown = sam.get("/api/models").json()["models"]
    assert {m["endpoint_id"]: m["remote"] for m in shown} == {home.id: False, far.id: True}
    assert "friend.example.net" not in sam.get("/api/models").text
    answer = admin.get(access_url(sam.user_id)).json()
    assert {m["endpoint_id"]: m["remote"] for m in answer["available"]} == {home.id: False, far.id: True}


def remembered_file(tmp_path, endpoint, content):
    mode = "wb" if isinstance(content, bytes) else "w"
    with open(tmp_path / "connection_models.json", mode) as f:
        f.write(content)
    return ConnectionHealth(lambda: [endpoint], lambda e: None, data_dir=str(tmp_path))


def entry_json(endpoint, models, saved_at="1000.5"):
    return '{"%s": {"models": %s, "saved_at": %s}}' % (endpoint.id, models, saved_at)


@pytest.mark.parametrize("damage", [
    b"\xff\xfe\x00broken", b"[" * 100000, ("[" + "9" * 5000 + "]").encode(), b"", b"{",
])
def test_a_damaged_remembered_file_is_treated_as_empty_and_starfire_still_starts(app, endpoint, tmp_path, damage):
    health = remembered_file(tmp_path, endpoint, damage)
    assert health.remembered_state(endpoint.id) is None


def test_a_remembered_time_that_cannot_be_a_time_drops_that_entry(app, endpoint, tmp_path):
    models = '[{"id": "gpt-4o", "endpoint_id": "%s"}]' % endpoint.id
    for saved_at in ("1" + "0" * 400, "NaN", "Infinity", '"soon"', "true"):
        health = remembered_file(tmp_path, endpoint, entry_json(endpoint, models, saved_at))
        assert health.remembered_state(endpoint.id) is None


def test_a_remembered_model_must_belong_to_its_connection_and_have_plain_fields(app, endpoint, tmp_path):
    ours = endpoint.id
    models = ('[{"id": "ok", "endpoint_id": "%s", "provider": "openai", "size": 12}, '
              '{"id": "other-connection", "endpoint_id": "someone-else"}, '
              '{"id": "bad-provider", "endpoint_id": "%s", "provider": 7}, '
              '{"id": "nan-size", "endpoint_id": "%s", "size": NaN}, '
              '{"id": "text-size", "endpoint_id": "%s", "size": "big"}, '
              '{"id": "no-extras", "endpoint_id": "%s"}]') % (ours, ours, ours, ours, ours)
    health = remembered_file(tmp_path, endpoint, entry_json(endpoint, models))
    assert [m["id"] for m in health.remembered_state(endpoint.id).models] == ["ok", "no-extras"]


def test_nan_in_the_remembered_file_cannot_break_the_model_list(app, endpoint, switchable_server, monkeypatch, tmp_path):
    models = '[{"id": "gpt-4o", "endpoint_id": "%s", "size": NaN}]' % endpoint.id
    monkeypatch.setattr(routes, "connection_health", remembered_file(tmp_path, endpoint, entry_json(endpoint, models)))
    switchable_server["up"] = False
    assert login(app, "sam@example.com").get("/api/models").status_code == 200


@pytest.mark.parametrize("url,remote", [
    ("http://nas/v1", False), ("http://arty-box:8080/v1", False), ("http://printer.local/v1", False),
    ("http://core.lan:8080/v1", False), ("http://router.home.arpa/v1", False),
    ("http://127.1:8080/v1", False), ("http://2130706433/v1", False), ("http://0x7f.0.0.1/v1", False),
    ("http://100.64.1.2/v1", True), ("https://example.com/v1", True), ("https://lan.example.com/v1", True),
])
def test_home_style_names_and_short_loopback_spellings_are_not_remote(app, url, remote):
    assert remote_rules.is_remote_connection(routes.endpoints.add(url, kind="api-key")) is remote


@pytest.mark.parametrize("url", [
    "http://[2606:4700::1111]/v1", "http://[2001:4860:4860::8888]/v1", "http://[64:ff9b::808:808]/v1",
    "http://134744072/v1", "http://0x08080808/v1", "http://8.8.8.8/v1",
])
def test_a_public_address_is_remote_however_it_is_written(app, url):
    assert remote_rules.is_remote_connection(routes.endpoints.add(url, kind="api-key")) is True


@pytest.mark.parametrize("url", ["http://home-server:8080/v1", "http://[::1]/v1", "http://[fd00::5]/v1", "http://0x0a000001/v1"])
def test_home_names_and_addresses_stay_home(app, url):
    assert remote_rules.is_remote_connection(routes.endpoints.add(url, kind="api-key")) is False


def test_an_ordinary_account_never_sees_a_connection_address(app, model_server):
    routes.endpoints.add("https://someone:hunter2@models.example.com:8443/v1", kind="api-key")
    labelled = routes.endpoints.add("https://other.example.com/v1", kind="api-key", label="Friend's server")
    sam, admin = login(app, "sam@example.com"), login(app, "admin@example.com")
    shown = {m["endpoint_id"]: m["connection"] for m in sam.get("/api/models").json()["models"]}
    assert set(shown.values()) == {"Connection 1", "Friend's server"} and shown[labelled.id] == "Friend's server"
    for_admin = {m["connection"] for m in admin.get("/api/models").json()["models"]}
    assert for_admin == {"models.example.com:8443", "Friend's server"}
    assert "hunter2" not in admin.get("/api/models").text + sam.get("/api/models").text


def test_a_thousand_ticked_models_can_be_saved(app, endpoint, model_server):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    names = [f"m{i}" for i in range(1000)]
    saved = admin.put(access_url(sam.user_id), json={"models": names})
    assert saved.status_code == 200 and saved.json()["models"] == names
    assert saved.json()["max_models"] == 1000


@pytest.mark.parametrize("url", ["/api/auth/new-account-models", "users"])
def test_a_save_without_a_models_field_is_a_bad_request(app, endpoint, model_server, url):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    target = access_url(sam.user_id) if url == "users" else url
    admin.put(target, json={"models": ["gpt-4o"]})
    refused = admin.put(target, json={})
    assert refused.status_code == 400
    assert admin.get(target).json()["models"] == ["gpt-4o"]


ODD_NAMES = ["mixtral[q4].gguf", "what?-7b", "star*model"]


@pytest.mark.parametrize("name", ODD_NAMES)
def test_a_ticked_name_with_wildcard_characters_is_that_name_only(name):
    ticked = model_access.escaped_name(name)
    assert model_access.literal_name(ticked) == name
    assert model_access.may_use(name, False, [ticked], [])
    assert not any(model_access.may_use(other, False, [ticked], []) for other in
                   ("mixtralq.gguf", "mixtral4.gguf", "whatX-7b", "star-anything-model", "starmodel"))


def test_an_escaped_name_opens_a_restricted_model_like_any_exact_name():
    assert model_access.may_use("odd[q4]-abl", False, [model_access.escaped_name("odd[q4]-abl")], ["*-abl"])
    assert not model_access.may_use("odd[q4]-abl", False, ["odd*"], ["*-abl"])


def test_a_name_with_brackets_shows_as_ticked_by_name_not_by_a_pattern(app, endpoint, monkeypatch):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(routes.httpx, "AsyncClient", lambda *args, **kwargs: real_client(
        *args, transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"data": [{"id": "mixtral[q4].gguf"}, {"id": "mixtralq.gguf"}]})), **kwargs))
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    saved = admin.put(access_url(sam.user_id), json={"models": [model_access.escaped_name("mixtral[q4].gguf")]}).json()
    row = available(saved)["mixtral[q4].gguf"]
    assert row["allowed"] is True and row["by_pattern"] is False
    assert available(saved)["mixtralq.gguf"]["allowed"] is False
    assert saved["kept"] == []
    assert listed(sam) == ["mixtral[q4].gguf"]


FILE_STYLE_RESTRICTED = ["E:\\models\\Qwen3-14B-abl.gguf", "e:\\models\\qwen3-14b-abl.Q4_K_M.gguf",
                         "/srv/models/Example-ABL.gguf", "qwen_abl.gguf", "qwen_abl_q4", "C:\\Models\\x-Abliterated.bin"]


@pytest.mark.parametrize("model", FILE_STYLE_RESTRICTED)
def test_file_style_names_of_restricted_models_are_restricted(model):
    defaults = type(config)().restricted_model_patterns()
    assert model_access.matches_any(model, defaults)


def test_a_windows_path_is_not_cut_down_to_its_drive_letter():
    assert model_access._without_tag("e:\\models\\x.gguf") == "e:\\models\\x.gguf"
    assert "e" not in model_access.name_forms("E:\\models\\x.gguf")
    assert not model_access.may_use("E:\\models\\x.gguf", False, ["e"], [])


def test_a_new_account_that_cannot_get_its_default_models_is_not_made(app, endpoint, model_server, monkeypatch):
    admin = login(app, "admin@example.com")
    admin.put("/api/auth/new-account-models", json={"models": ["gpt-4o"]})

    def broken_disk(self, user_id, patterns):
        raise OSError("disk full")

    monkeypatch.setattr(ModelAccessStore, "set_allowed", broken_disk)
    refused = admin.post("/api/auth/users", json={"email": "kim@example.com", "name": "Kim", "password": PASSWORD})
    assert refused.status_code == 500
    assert auth_routes.users.find_by_email("kim@example.com") is None
