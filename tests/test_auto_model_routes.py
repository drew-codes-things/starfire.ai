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
from generation_quota import GenerationQuota
from model_access import ModelAccessStore
from model_endpoints import ModelEndpointStore

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}
VISION, GENERAL, SMALL, CODER, ARTY, RESTRICTED = (
    "vision-vl:7b", "general:14b", "small-chat:9b", "coder:7b", "Arty", "restricted-abl")
LISTED = [VISION, RESTRICTED, SMALL, CODER, GENERAL, ARTY]
TINY_PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                         "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def fresh_store(monkeypatch, name, build):
    if hasattr(routes, name):
        monkeypatch.setattr(routes, name, build())


@pytest.fixture
def app(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    users, sessions = UserStore(data_dir), SessionStore(data_dir)
    monkeypatch.setattr(config, "data_dir", data_dir)
    monkeypatch.setattr(config, "ollama_base_url", None)
    monkeypatch.setattr(config, "task_model", GENERAL)
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(auth_routes, "known_addresses", auth.KnownAddresses(data_dir))
    monkeypatch.setattr(routes, "registry", user_data.configure(data_dir))
    monkeypatch.setattr(routes, "api_keys", APIKeyManager(data_dir))
    monkeypatch.setattr(routes, "endpoints", ModelEndpointStore(data_dir))
    monkeypatch.setattr(routes, "model_access_store", ModelAccessStore(data_dir))
    monkeypatch.setattr(routes, "generation_quota", GenerationQuota())
    fresh_store(monkeypatch, "auto_model_settings", lambda: routes.auto_model.AutoModelSettings(data_dir))
    fresh_store(monkeypatch, "reply_runs", lambda: routes.ReplyRuns())
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

    def answer(request):
        return httpx.Response(200, json={"data": [{"id": name} for name in LISTED]})

    def client(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(answer), **kwargs)

    monkeypatch.setattr(routes.httpx, "AsyncClient", client)


@pytest.fixture
def replies(monkeypatch):
    asked = []

    async def fake_stream(base_url, api_key, model, messages, system, options):
        asked.append(model)
        yield chat_stream._sse_line({"delta": f"answer from {model}"})
        yield chat_stream._sse_line({"done": True})

    monkeypatch.setattr(routes, "stream_chat", fake_stream)
    return asked


def login(app, email):
    client = TestClient(app, base_url="https://testserver", headers=HEADERS)
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    client.user_id = auth_routes.users.find_by_email(email).id
    return client


def events(response):
    return [json.loads(line[len("data:"):]) for line in response.text.split("\n\n") if line.startswith("data:")]


def ask_auto(client, content, attachments=None):
    message = {"role": "user", "content": content, **({"attachments": attachments} if attachments else {})}
    return client.post("/api/chat_stream", json={"endpoint_id": "auto", "model": "auto", "messages": [message]})


def picked(response):
    assert response.status_code == 200, response.text
    return next(e["model_choice"] for e in events(response) if "model_choice" in e)


def test_auto_sends_each_prompt_to_its_role_model_and_says_which(app, endpoint, model_server, replies):
    sam = login(app, "sam@example.com")
    image = sam.post("/api/attachments", files={"file": ("dot.png", TINY_PNG, "image/png")}).json()
    answers = {
        "plain": picked(ask_auto(sam, "what is a good name for a cat")),
        "code": picked(ask_auto(sam, "```py\nprint(1)\n```\nwhy?")),
        "agent": picked(ask_auto(sam, "remind me to stretch in an hour")),
        "image": picked(ask_auto(sam, "what is in this picture", [image["id"]])),
    }
    assert {name: (a["model"], a["role"]) for name, a in answers.items()} == {
        "plain": (GENERAL, "chat"), "code": (CODER, "code"), "agent": (ARTY, "agent"), "image": (VISION, "vision")}
    assert replies == [GENERAL, CODER, ARTY, VISION]
    assert all(a["auto"] is True and a["endpoint_id"] == endpoint.id for a in answers.values())


def test_auto_never_sends_a_normal_account_to_a_restricted_model_an_admin_named(app, endpoint, model_server, replies):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    saved = admin.put("/api/auto-model", json={"roles": {"chat": RESTRICTED, "code": RESTRICTED}})
    assert saved.status_code == 200 and saved.json()["roles"]["chat"] == RESTRICTED
    assert picked(ask_auto(sam, "hello there"))["model"] == GENERAL
    assert picked(ask_auto(sam, "```py\nx\n```"))["model"] == CODER
    assert picked(ask_auto(admin, "hello there"))["model"] == RESTRICTED
    assert RESTRICTED not in replies[:2]


def test_auto_keeps_to_the_models_an_admin_allowed_for_the_account(app, endpoint, model_server, replies):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    assert admin.put(f"/api/auth/users/{sam.user_id}/models", json={"models": [SMALL]}).status_code == 200
    for prompt in ("hello", "```py\nx\n```", "remind me to stretch"):
        assert picked(ask_auto(sam, prompt))["model"] == SMALL
    assert admin.put(f"/api/auth/users/{sam.user_id}/models", json={"models": []}).status_code == 200
    refused = ask_auto(sam, "hello")
    assert refused.status_code == 503 and "Auto found no model" in refused.json()["detail"]
    assert replies == [SMALL, SMALL, SMALL]


def test_a_wildcard_allow_list_does_not_let_auto_send_an_account_to_a_restricted_model(app, endpoint, model_server,
                                                                                        replies, monkeypatch):
    monkeypatch.setattr(config, "restricted_models", "*-abl,*-abl-*,*abliterated*")
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    assert admin.put(f"/api/auth/users/{sam.user_id}/models", json={"models": ["*"]}).status_code == 200
    assert admin.put("/api/auto-model", json={"roles": {"chat": RESTRICTED, "code": RESTRICTED}}).status_code == 200
    picks = [picked(ask_auto(sam, text))["model"] for text in ("hello", "```py\nx\n```")]
    assert RESTRICTED not in picks and RESTRICTED not in replies
    assert admin.put(f"/api/auth/users/{sam.user_id}/models", json={"models": [RESTRICTED]}).status_code == 200
    assert picked(ask_auto(sam, "hello"))["model"] == RESTRICTED


def test_only_an_admin_sees_or_changes_which_models_auto_uses(app, endpoint, model_server):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    assert sam.get("/api/auto-model").status_code == 403
    assert sam.put("/api/auto-model", json={"roles": {"chat": SMALL}}).status_code == 403
    view = admin.get("/api/auto-model").json()
    assert view["picks"]["code"] == {"model": CODER, "filled_by": "code"}
    assert admin.put("/api/auto-model", json={"roles": {"writing": SMALL}}).status_code == 400


def test_the_model_list_names_the_everyday_model_auto_falls_back_on(app, endpoint, model_server):
    sam = login(app, "sam@example.com")
    assert sam.get("/api/models").json()["auto_everyday"] == {"endpoint_id": endpoint.id, "model": GENERAL}


def test_an_accounts_last_model_choice_is_kept_on_the_server_and_defaults_to_auto(app, endpoint, model_server):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    assert sam.get("/api/model-choice").json() == {"endpoint_id": "auto", "model": "auto"}
    assert sam.put("/api/model-choice", json={"endpoint_id": endpoint.id, "model": SMALL}).status_code == 200
    assert login(app, "sam@example.com").get("/api/model-choice").json() == {"endpoint_id": endpoint.id, "model": SMALL}
    assert admin.get("/api/model-choice").json()["model"] == "auto"
    refused = sam.put("/api/model-choice", json={"endpoint_id": endpoint.id, "model": RESTRICTED})
    assert refused.status_code == 403
    assert sam.put("/api/model-choice", json={"endpoint_id": "auto", "model": "auto"}).json()["model"] == "auto"


def test_a_kept_choice_the_account_may_no_longer_use_reads_as_auto(app, endpoint, model_server):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    sam.put("/api/model-choice", json={"endpoint_id": endpoint.id, "model": SMALL})
    admin.put(f"/api/auth/users/{sam.user_id}/models", json={"models": [GENERAL]})
    assert sam.get("/api/model-choice").json()["model"] == "auto"


def test_a_chat_keeps_auto_as_its_model(app, endpoint):
    sam = login(app, "sam@example.com")
    session = sam.post("/api/chat/sessions", json={"title": "t"}).json()
    sam.put(f"/api/chat/sessions/{session['id']}", json={"endpoint_id": "auto", "model": "auto"})
    reopened = login(app, "sam@example.com").get(f"/api/chat/sessions/{session['id']}").json()
    assert (reopened["endpoint_id"], reopened["model"]) == ("auto", "auto")


TAGGED_RESTRICTED = ["restricted-abl:latest", "library/coder-abl:q4", "Vision-VL-ABL:7B", "chat-abliterated:14b"]


@pytest.fixture
def tagged_model_server(monkeypatch):
    real_client = httpx.AsyncClient

    def answer(request):
        return httpx.Response(200, json={"data": [{"id": name} for name in TAGGED_RESTRICTED + [GENERAL]]})

    monkeypatch.setattr(routes.httpx, "AsyncClient",
                        lambda *args, **kwargs: real_client(*args, transport=httpx.MockTransport(answer), **kwargs))


def test_auto_never_picks_a_restricted_model_spelled_with_a_tag_or_namespace(app, endpoint, tagged_model_server,
                                                                              replies, monkeypatch):
    monkeypatch.setattr(config, "restricted_models", "*-abl,*-abl-*,*abliterated*")
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    assert admin.put("/api/auto-model", json={"roles": {"chat": TAGGED_RESTRICTED[0],
                                                        "code": TAGGED_RESTRICTED[1]}}).status_code == 200
    asked = ["hello there", "```py\nx\n```", "remind me to stretch", "what is a good name for a cat"]
    picks = [picked(ask_auto(sam, text))["model"] for text in asked]
    assert picks == [GENERAL] * len(asked) and set(replies).isdisjoint(TAGGED_RESTRICTED)
