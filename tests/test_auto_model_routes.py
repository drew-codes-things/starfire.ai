import asyncio
import base64
import json
import os
import sys
from types import SimpleNamespace

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
    return routes.endpoints.add("http://192.168.1.60:8080/v1", kind="api-key")


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


@pytest.fixture
def tool_turns(monkeypatch):
    seen = []

    async def with_tools(base_url, api_key, model, messages, system, options, servers, tools, ctx, max_tools=None):
        seen.append((model, ctx))
        yield chat_stream._sse_line({"done": True})

    monkeypatch.setattr(routes, "run_chat_with_tools", with_tools)
    return seen


@pytest.fixture
def viewer(monkeypatch):
    asked = []

    async def seeing_reply(base_url, api_key, model, messages, system, options, tools=None, **kwargs):
        asked.append((base_url, model, messages))
        yield ("delta", "Discord is open on #general.")

    monkeypatch.setattr(routes.screen_look, "stream_chat_once", seeing_reply)
    return asked


def ask_from_desktop(client, content, model="auto", endpoint_id="auto", attachments=None):
    message = {"role": "user", "content": content, **({"attachments": attachments} if attachments else {})}
    return client.post("/api/chat_stream", json={"endpoint_id": endpoint_id, "model": model, "messages": [message],
                                                 "desktop_screen": True})


SCREEN_PICTURE = {"mime": "image/png", "image": base64.b64encode(TINY_PNG).decode(), "caption": "caption"}


def viewing_for(endpoint, model):
    return asyncio.run(routes._plan_screen_viewing(endpoint, None, model))


LOCAL_ENGINE, OTHER_LOCAL_ENGINE, REMOTE_A_ENGINE = (
    "http://192.168.1.40:11434", "http://192.168.1.50:11434", "http://remote-a.example.com:11434")
REMOTE_A_VISION, OTHER_VISION, REMOTE_VISION = "llava-remote:7b", "other-vl:3b", "remote/remote-a/qwen-vl"


@pytest.fixture
def engine(app, monkeypatch):
    seeing = {VISION: ["completion", "vision"], GENERAL: ["completion", "tools"],
              REMOTE_A_VISION: ["completion", "vision"], OTHER_VISION: ["completion", "vision"],
              REMOTE_VISION: ["completion", "vision"]}
    engine_endpoint = routes.endpoints.add(LOCAL_ENGINE, kind="ollama")
    listed = [{"id": VISION, "endpoint_id": engine_endpoint.id}, {"id": GENERAL, "endpoint_id": engine_endpoint.id}]
    asked_capabilities = []

    async def usable():
        return list(listed)

    async def capabilities(base_url, api_key, model):
        asked_capabilities.append((base_url, model))
        return seeing.get(model)

    monkeypatch.setattr(routes, "_usable_models", usable)
    monkeypatch.setattr(routes.screen_look, "ollama_capabilities", capabilities)
    return SimpleNamespace(endpoint=engine_endpoint, seeing=seeing, listed=listed, asked=asked_capabilities)


def add_listed(engine, address, model, kind="ollama"):
    connection = routes.endpoints.add(address, kind=kind)
    engine.listed.insert(0, {"id": model, "endpoint_id": connection.id})
    return connection


def configure_vision(monkeypatch, model):
    monkeypatch.setattr(routes, "_role_sources",
                        lambda usable: routes.auto_model.RoleSources(configured={routes.auto_model.VISION: model}))


def test_a_model_the_engine_says_is_blind_gets_the_screen_described_by_a_local_vision_model(engine, viewer):
    viewing = viewing_for(engine.endpoint, GENERAL)
    assert viewing.describe is not None and not viewing.unseen and viewing.described_by == VISION
    assert asyncio.run(viewing.describe(SCREEN_PICTURE)) == "Discord is open on #general."
    (base_url, seeing_model, messages), = viewer
    assert (base_url, seeing_model) == (engine.endpoint.base_url, VISION)
    assert messages[-1]["images"] == [SCREEN_PICTURE["image"]]


def test_a_model_the_engine_says_sees_is_handed_the_screen_itself(engine, viewer):
    assert viewing_for(engine.endpoint, VISION) == routes.screen_look.SEEN_DIRECTLY


@pytest.mark.parametrize("address,model", [("https://openrouter.ai/api/v1", "openai/gpt-4o"),
                                           ("http://lan-box:8080/v1", "gemma3:12b"),
                                           ("https://api.mistral.ai/v1", "pixtral-12b"),
                                           ("https://api.openai.com/v1", "gpt-3.5-turbo")])
def test_a_model_nobody_says_is_blind_is_handed_the_screen_as_before(engine, viewer, address, model):
    chosen = routes.endpoints.add(address, kind="api-key")
    assert viewing_for(chosen, model) == routes.screen_look.SEEN_DIRECTLY
    assert viewer == []


def test_an_engine_that_cannot_say_leaves_the_model_to_see_the_screen_itself(engine, viewer):
    engine.seeing.pop(GENERAL)
    assert viewing_for(engine.endpoint, GENERAL) == routes.screen_look.SEEN_DIRECTLY


def test_a_blind_model_with_no_confirmed_local_viewer_is_told_so_before_anything_is_captured(engine, viewer):
    engine.seeing[VISION] = ["completion"]
    assert viewing_for(engine.endpoint, GENERAL).unseen
    engine.seeing.pop(VISION)
    assert viewing_for(engine.endpoint, GENERAL).unseen


def test_a_vision_model_on_a_remote_connection_never_describes_the_screen(engine, viewer, monkeypatch):
    engine.listed.remove({"id": VISION, "endpoint_id": engine.endpoint.id})
    remote_a = add_listed(engine, REMOTE_A_ENGINE, REMOTE_A_VISION)
    engine.listed.insert(0, {"id": REMOTE_VISION, "endpoint_id": engine.endpoint.id})
    configure_vision(monkeypatch, REMOTE_A_VISION)
    assert viewing_for(engine.endpoint, GENERAL).unseen
    assert (remote_a.base_url, REMOTE_A_VISION) not in engine.asked and (LOCAL_ENGINE, REMOTE_VISION) not in engine.asked
    assert viewer == []


def test_a_rejected_auto_vision_pick_falls_through_to_the_next_local_vision_model(engine, viewer, monkeypatch):
    add_listed(engine, REMOTE_A_ENGINE, REMOTE_A_VISION)
    other = add_listed(engine, OTHER_LOCAL_ENGINE, OTHER_VISION)
    engine.listed.remove({"id": VISION, "endpoint_id": engine.endpoint.id})
    configure_vision(monkeypatch, REMOTE_A_VISION)
    viewing = viewing_for(engine.endpoint, GENERAL)
    assert viewing.described_by == OTHER_VISION
    asyncio.run(viewing.describe(SCREEN_PICTURE))
    assert viewer[0][:2] == (other.base_url, OTHER_VISION)


def test_a_vision_model_on_a_connection_under_arty_s_rules_never_describes_the_screen(engine, viewer, monkeypatch):
    monkeypatch.setattr(routes.remote_rules, "applies_to", lambda checked, listed: checked.id == engine.endpoint.id)
    assert viewing_for(engine.endpoint, GENERAL).unseen


def test_every_desktop_chat_with_tools_gets_a_viewing_plan(app, endpoint, model_server, tool_turns):
    admin = login(app, "admin@example.com")
    assert picked(ask_from_desktop(admin, "yes please, have a look"))["model"] == GENERAL
    assert ask_from_desktop(admin, "have a look", model=GENERAL, endpoint_id=endpoint.id).status_code == 200
    assert [ctx.plan_screen_viewing is not None for _model, ctx in tool_turns] == [True, True]


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
        "plain": (GENERAL, "chat"), "code": (GENERAL, "code"), "agent": (ARTY, "agent"), "image": (VISION, "vision")}
    assert replies == [GENERAL, GENERAL, ARTY, VISION]
    assert all(a["auto"] is True and a["endpoint_id"] == endpoint.id for a in answers.values())


def test_auto_never_sends_a_normal_account_to_a_restricted_model_an_admin_named(app, endpoint, model_server, replies):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    saved = admin.put("/api/auto-model", json={"roles": {"chat": RESTRICTED, "code": RESTRICTED}})
    assert saved.status_code == 200 and saved.json()["roles"]["chat"] == RESTRICTED
    assert picked(ask_auto(sam, "hello there"))["model"] == GENERAL
    assert picked(ask_auto(sam, "```py\nx\n```"))["model"] == GENERAL
    assert picked(ask_auto(admin, "hello there"))["model"] == RESTRICTED
    assert RESTRICTED not in replies[:2]


def test_auto_keeps_to_the_models_an_admin_allowed_for_the_account(app, endpoint, model_server, replies):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    assert admin.put(f"/api/auth/users/{sam.user_id}/models", json={"models": [SMALL]}).status_code == 200
    for prompt in ("hello", "```py\nx\n```", "remind me to stretch"):
        refused = ask_auto(sam, prompt)
        assert refused.status_code == 503 and "Auto found no model" in refused.json()["detail"]
    assert admin.put(f"/api/auth/users/{sam.user_id}/models", json={"models": []}).status_code == 200
    refused = ask_auto(sam, "hello")
    assert refused.status_code == 503 and "Auto found no model" in refused.json()["detail"]
    assert replies == []


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
    assert view["picks"]["code"] == {"model": GENERAL, "filled_by": "chat"}
    assert admin.put("/api/auto-model", json={"roles": {"writing": SMALL}}).status_code == 400


def test_the_model_list_names_the_everyday_model_auto_falls_back_on(app, endpoint, model_server):
    sam = login(app, "sam@example.com")
    assert sam.get("/api/models").json()["auto_everyday"] == {"endpoint_id": endpoint.id, "model": GENERAL}


def test_an_accounts_last_model_choice_is_kept_on_the_server_and_starts_as_nothing_chosen(app, endpoint, model_server):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    assert sam.get("/api/model-choice").json() == {"endpoint_id": "", "model": ""}
    assert sam.put("/api/model-choice", json={"endpoint_id": endpoint.id, "model": SMALL}).status_code == 200
    assert login(app, "sam@example.com").get("/api/model-choice").json() == {"endpoint_id": endpoint.id, "model": SMALL}
    assert admin.get("/api/model-choice").json()["model"] == ""
    refused = sam.put("/api/model-choice", json={"endpoint_id": endpoint.id, "model": RESTRICTED})
    assert refused.status_code == 403
    assert sam.put("/api/model-choice", json={"endpoint_id": "auto", "model": "auto"}).json()["model"] == "auto"


def test_a_kept_choice_the_account_may_no_longer_use_reads_as_nothing_chosen(app, endpoint, model_server):
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    sam.put("/api/model-choice", json={"endpoint_id": endpoint.id, "model": SMALL})
    admin.put(f"/api/auth/users/{sam.user_id}/models", json={"models": [GENERAL]})
    assert sam.get("/api/model-choice").json()["model"] == ""


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


@pytest.fixture
def remote_models(app, endpoint, monkeypatch):
    remote = routes.endpoints.add("https://remote-a.example.com/v1", kind="api-key")
    local = [{"id": name, "endpoint_id": endpoint.id} for name in (SMALL, GENERAL)]
    outside = [{"id": name, "endpoint_id": remote.id} for name in (VISION, CODER, GENERAL, ARTY, "remote-model-a")]
    listed = outside + local

    async def usable():
        return list(listed)

    monkeypatch.setattr(routes, "_usable_models", usable)
    return SimpleNamespace(remote=remote, local=endpoint, listed=listed)


def test_with_default_settings_no_auto_route_reaches_a_remote_connection(app, remote_models, model_server, replies,
                                                                       monkeypatch):
    monkeypatch.setattr(config, "task_model", "")
    admin = login(app, "admin@example.com")
    image = admin.post("/api/attachments", files={"file": ("dot.png", TINY_PNG, "image/png")}).json()
    prompts = [("hello", None), ("```py\nx\n```", None), ("remind me to stretch", None), ("what is this", [image["id"]])]
    picks = [picked(ask_auto(admin, text, attachments)) for text, attachments in prompts]
    assert {pick["endpoint_id"] for pick in picks} == {remote_models.local.id}
    view = admin.get("/api/auto-model").json()["picks"]
    assert {pick["model"] for pick in view.values()} <= {SMALL, GENERAL}
    everyday = admin.get("/api/models").json()["auto_everyday"]
    assert everyday is None or everyday["endpoint_id"] == remote_models.local.id


def test_a_remote_model_named_as_the_coding_model_is_not_used(app, remote_models, model_server, replies, caplog):
    admin = login(app, "admin@example.com")
    assert admin.put("/api/auto-model", json={"roles": {"code": "remote-model-a"}}).status_code == 200
    pick = picked(ask_auto(admin, "```py\nx\n```"))
    assert pick["endpoint_id"] == remote_models.local.id and pick["model"] != "remote-model-a"
    assert "remote-model-a" in caplog.text


def test_everyday_and_coding_messages_follow_the_default_and_coding_settings(app, endpoint, model_server, replies):
    admin = login(app, "admin@example.com")
    assert [picked(ask_auto(admin, text))["model"] for text in ("hello", "```py\nx\n```")] == [GENERAL, GENERAL]
    assert admin.put("/api/auto-model", json={"roles": {"chat": SMALL}}).status_code == 200
    assert [picked(ask_auto(admin, text))["model"] for text in ("hello", "```py\nx\n```")] == [SMALL, SMALL]
    assert admin.put("/api/auto-model", json={"roles": {"chat": SMALL, "code": CODER}}).status_code == 200
    assert [picked(ask_auto(admin, text))["model"] for text in ("hello", "```py\nx\n```")] == [SMALL, CODER]
    assert admin.get("/api/models").json()["auto_everyday"]["model"] == SMALL


def test_a_picture_goes_to_the_default_model_when_it_can_see_and_to_the_vision_helper_when_it_cannot(app, engine,
                                                                                                    model_server, replies):
    admin = login(app, "admin@example.com")
    image = admin.post("/api/attachments", files={"file": ("dot.png", TINY_PNG, "image/png")}).json()
    assert picked(ask_auto(admin, "what is this", [image["id"]]))["model"] == VISION
    engine.seeing[GENERAL] = ["completion", "vision"]
    seen = picked(ask_auto(admin, "what is this", [image["id"]]))
    assert (seen["model"], seen["role"], seen["filled_by"]) == (GENERAL, "vision", "chat")
    assert admin.put("/api/auto-model", json={"roles": {"vision": VISION}}).status_code == 200
    assert picked(ask_auto(admin, "what is this", [image["id"]]))["model"] == VISION


def test_the_default_model_setting_becomes_the_main_for_the_context_and_helpers(app, endpoint, model_server,
                                                                              monkeypatch):
    admin = login(app, "admin@example.com")
    assert routes.main_model() == GENERAL
    assert admin.put("/api/auto-model", json={"roles": {"chat": CODER}}).status_code == 200
    assert routes.main_model() == CODER
    assert chat_stream.is_the_main_on_its_engine("http://127.0.0.1:11434", CODER)
    assert not chat_stream.is_the_main_on_its_engine("http://127.0.0.1:11434", GENERAL)
    assert chat_stream.fixed_context_for("http://127.0.0.1:11434", CODER) == config.task_num_ctx
    assert chat_stream.fixed_context_for("http://127.0.0.1:11434", GENERAL) == 0


@pytest.mark.parametrize("saved", [ARTY, "retired-model:9b", "arty/general:14b"])
def test_a_saved_default_model_that_is_not_a_local_model_falls_back_to_the_task_model(app, endpoint, model_server,
                                                                                       monkeypatch, caplog, saved):
    admin = login(app, "admin@example.com")
    assert admin.put("/api/auto-model", json={"roles": {"chat": saved}}).status_code == 200
    assert routes.main_model() == GENERAL
    assert chat_stream.is_the_main_on_its_engine("http://127.0.0.1:11434", GENERAL)
    assert chat_stream.fixed_context_for("http://127.0.0.1:11434", GENERAL) == config.task_num_ctx
    assert saved in caplog.text


def test_the_auto_settings_offer_only_local_models_for_the_default_and_coding_roles(app, remote_models, model_server):
    admin = login(app, "admin@example.com")
    view = admin.get("/api/auto-model").json()
    assert sorted(view["local_models"]) == sorted([SMALL, GENERAL])


def test_auto_is_refused_cleanly_for_an_account_with_only_remote_models(app, monkeypatch):
    remote = routes.endpoints.add("https://remote-a.example.com/v1", kind="api-key")

    async def usable():
        return [{"id": "remote-model-a", "endpoint_id": remote.id}]

    monkeypatch.setattr(routes, "_usable_models", usable)
    response = ask_auto(login(app, "sam@example.com"), "hello")
    assert response.status_code == 503 and response.json()["detail"] == routes.MAIN_NOT_LISTED.format(main=config.task_model)
