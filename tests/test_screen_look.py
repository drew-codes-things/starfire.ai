import asyncio
import base64
import json
import os
import sys
from types import SimpleNamespace

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import agent_loop
import auth
import auth_routes
import builtin_tools
import chat_stream
import model_capabilities
import routes
import screen_look
import user_data
from api_key_manager import APIKeyManager
from auth import SessionStore, UserStore
from config import config
from model_endpoints import ModelEndpointStore

pytestmark = pytest.mark.usefixtures("unreadable_model_lists")

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}
JPEG = base64.b64encode(b"\xff\xd8\xff\xe0 pretend this is a picture").decode()
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n pretend").decode()
TOOL = "look_at_screen"


@pytest.fixture(autouse=True)
def nothing_left_waiting():
    yield
    screen_look._pending.clear()


class Context(SimpleNamespace):
    async def emit(self, event):
        if self.events is not None:
            await self.events.put(event)


def looking(admin=True, desktop=True, with_events=True):
    return Context(may_use_host_tools=admin, desktop_screen=desktop,
                   events=asyncio.Queue() if with_events else None, screen_images=[], untrusted_read=False)


async def answered(ctx, outcome, owner="owner"):
    request = await asyncio.wait_for(ctx.events.get(), 5)
    request_id = request["screen_request"]["request_id"]
    screen_look.claim(request_id, owner)
    screen_look.resolve(request_id, owner, outcome)
    return request["screen_request"]


def run_tool(ctx, arguments, outcome, owner="owner"):
    async def go():
        token = user_data.current_user_id.set(owner)
        try:
            task = asyncio.ensure_future(builtin_tools.call_builtin_tool(TOOL, arguments, ctx))
            if outcome is None:
                return await task, None
            seen = await answered(ctx, outcome, owner)
            return await task, seen
        finally:
            user_data.current_user_id.reset(token)
    return asyncio.run(go())


def picture(**fields):
    return {"ok": True, "image": JPEG, "mime": "image/jpeg", "target": "your whole screen",
            "window": "#general - Discord", "note": "Discord is in front.", **fields}


def failure(reason, detail=""):
    return {"ok": False, "reason": reason, "detail": detail}


def test_the_tool_is_in_the_catalogue_and_counts_as_untrusted_content():
    assert TOOL in {schema["function"]["name"] for schema in builtin_tools.TOOL_SCHEMAS}
    assert builtin_tools.reads_untrusted_content(TOOL, {})
    assert not builtin_tools.changes_state(TOOL, {})
    assert TOOL in model_capabilities.BASIC_TIER_TOOLS


def test_a_picture_is_kept_for_the_model_and_the_page_is_asked_first():
    ctx = looking()
    result, request = run_tool(ctx, {"app": "Discord"}, picture())
    assert request["app"] == "Discord"
    assert result.startswith("Took a screenshot of your whole screen")
    assert "Discord is in front." in result and "#general - Discord" in result
    assert "untrusted" in result.lower()
    assert ctx.screen_images == [{"mime": "image/jpeg", "image": JPEG, "caption": result}]


def test_no_app_means_the_whole_screen():
    ctx = looking()
    _result, request = run_tool(ctx, {}, picture(window=""))
    assert request["app"] == ""


@pytest.mark.parametrize("reason,words", [
    ("declined", "said no"),
    ("timed_out", "in time"),
    ("not_running", "not running"),
    ("not_linked", "not linked"),
    ("not_found", "no window"),
    ("unavailable", "turned off"),
    ("cannot_see", "cannot take screenshots"),
    ("off_limits", "never looked at"),
    ("busy", "already waiting"),
    ("ambiguous", "more than one window"),
    ("changed", "changed"),
    ("failed", "failed"),
])
def test_every_way_it_can_fail_tells_the_model_to_say_so_and_gives_it_no_picture(reason, words):
    ctx = looking()
    result, _request = run_tool(ctx, {"app": "Discord"}, failure(reason))
    assert result.startswith("error: could not look at the window Discord")
    assert words in result
    assert "Tell the user plainly that you could not look" in result
    assert "Do not describe or guess" in result
    assert ctx.screen_images == []


def test_an_unknown_reason_still_fails_honestly():
    result, _request = run_tool(looking(), {"app": ""}, failure("something odd"))
    assert result.startswith("error: could not look at the screen") and "Nothing was captured" in result


def test_what_the_page_says_went_wrong_is_shown_cleaned_and_short():
    detail = "There is no window for Discord open.‮" + "x" * 1000
    result, _request = run_tool(looking(), {"app": "Discord"}, failure("not_found", detail))
    assert "There is no window for Discord open." in result and "‮" not in result
    assert len(result) < 900


def test_an_ordinary_account_cannot_look():
    result, request = run_tool(looking(admin=False), {"app": ""}, None)
    assert result.startswith("error: only an admin account can use look_at_screen")
    assert screen_look._pending == {}


def test_a_chat_that_did_not_come_from_starfire_desktop_cannot_look():
    result, _request = run_tool(looking(desktop=False), {"app": ""}, None)
    assert result.startswith("error: look_at_screen is only available in starfire Desktop")
    assert screen_look._pending == {}


def test_a_task_with_no_one_to_ask_cannot_look():
    result, _request = run_tool(looking(with_events=False), {"app": ""}, None)
    assert result == builtin_tools.NO_ONE_TO_ASK
    assert screen_look._pending == {}


@pytest.mark.parametrize("arguments", [{"app": 5}, {"app": "x" * 81}, {"app": "Dis‮cord"}, {"app": "Dis\ncord"}, {"app": ["Discord"]}])
def test_a_bad_app_name_is_refused_without_asking_the_page(arguments):
    ctx = looking()
    result, _request = run_tool(ctx, arguments, None)
    assert result.startswith("error: ") and "app" in result
    assert screen_look._pending == {}
    assert ctx.events.empty()


def test_the_wait_ends_with_a_timeout_failure_when_nobody_answers(monkeypatch):
    monkeypatch.setattr(screen_look, "LOOK_TIMEOUT_SECONDS", 0.05)
    result, _request = run_tool(looking(), {"app": "Discord"}, None)
    assert result.startswith("error: could not look at the window Discord") and "in time" in result
    assert screen_look._pending == {}


def stage(owner="owner", app="Discord"):
    async def go():
        return screen_look.stage(app, owner)
    return asyncio.run(go())


def test_a_request_can_be_claimed_once_by_its_owner():
    async def go():
        request_id = screen_look.stage("Discord", "owner")
        with pytest.raises(screen_look.NotWaiting):
            screen_look.claim(request_id, "someone else")
        assert screen_look.claim(request_id, "owner") == "Discord"
        with pytest.raises(screen_look.AlreadyClaimed):
            screen_look.claim(request_id, "owner")
        with pytest.raises(screen_look.NotWaiting):
            screen_look.claim("nothing", "owner")
    asyncio.run(go())


def test_only_the_owner_can_answer_and_only_once():
    async def go():
        request_id = screen_look.stage("Discord", "owner")
        assert screen_look.resolve(request_id, "someone else", picture()) is False
        assert screen_look.resolve(request_id, "owner", picture()) is True
        assert screen_look.resolve(request_id, "owner", picture()) is False
    asyncio.run(go())


@pytest.mark.parametrize("outcome", [
    picture(image=""), picture(image="not base64 !!"), picture(mime="text/html"), picture(mime="image/png"),
    picture(image=base64.b64encode(b"GIF89a").decode()),
    picture(image=base64.b64encode(b"\xff\xd8\xff" + b"0" * 5_000_000).decode()),
])
def test_a_picture_that_is_not_a_picture_is_refused(outcome):
    async def go():
        request_id = screen_look.stage("Discord", "owner")
        with pytest.raises(screen_look.BadPicture):
            screen_look.resolve(request_id, "owner", outcome)
        assert screen_look.resolve(request_id, "owner", failure("failed")) is True
    asyncio.run(go())


def test_a_png_is_accepted_when_it_says_so():
    async def go():
        request_id = screen_look.stage("Discord", "owner")
        assert screen_look.resolve(request_id, "owner", picture(image=PNG, mime="image/png")) is True
    asyncio.run(go())


@pytest.mark.parametrize("provider,expected", [
    ("ollama", lambda m: m["images"] == [JPEG] and isinstance(m["content"], str)),
    ("openai", lambda m: m["content"][1] == {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{JPEG}"}}),
    ("anthropic", lambda m: m["content"][1] == {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": JPEG}}),
])
def test_the_picture_reaches_the_model_in_the_form_each_provider_takes(provider, expected):
    message = screen_look.image_message(provider, {"mime": "image/jpeg", "image": JPEG, "caption": "caption"})
    assert message["role"] == "user" and expected(message)
    assert "caption" in (message["content"] if isinstance(message["content"], str) else message["content"][0]["text"])


class Scripted:
    def __init__(self, *rounds):
        self.rounds = list(rounds)
        self.seen = []
        self.tools = []

    async def __call__(self, base_url, api_key, model, messages, system, options, tools=None, think=None, **kwargs):
        self.seen.append([dict(m) for m in messages])
        self.tools.append([t["function"]["name"] for t in tools or []])
        for item in self.rounds.pop(0):
            yield item


def collect_loop(monkeypatch, base_url, ctx, enabled, outcome):
    scripted = Scripted([("tool_calls", [{"id": "c1", "name": TOOL, "arguments": {"app": "Discord"}}])], [("delta", "It says hi.")])
    monkeypatch.setattr(agent_loop, "stream_chat_once", scripted)

    async def go():
        token = user_data.current_user_id.set("owner")
        try:
            chunks = []
            async for chunk in agent_loop.run_chat_with_tools(base_url, None, "m", [{"role": "user", "content": "look at my discord"}],
                                                              None, None, set(), enabled, ctx):
                chunks.append(json.loads(chunk.decode()[len("data:"):]))
                if "screen_request" in chunks[-1]:
                    request_id = chunks[-1]["screen_request"]["request_id"]
                    screen_look.claim(request_id, "owner")
                    screen_look.resolve(request_id, "owner", outcome)
            return chunks
        finally:
            user_data.current_user_id.reset(token)
    return scripted, asyncio.run(go())


def test_the_loop_hands_the_model_the_picture_after_the_tool_result_and_marks_the_chat_untrusted(monkeypatch):
    scripted, events = collect_loop(monkeypatch, "http://model:11434", looking(), {TOOL}, picture())
    assert scripted.tools[0] == [TOOL]
    request = next(e for e in events if "screen_request" in e)["screen_request"]
    assert request["app"] == "Discord" and len(request["request_id"]) >= 12
    second_round = scripted.seen[1]
    assert second_round[-2]["role"] == "tool" and second_round[-2]["content"].startswith("Took a screenshot")
    assert second_round[-1]["role"] == "user" and second_round[-1]["images"] == [JPEG]
    assert {"untrusted_read": True} in events
    assert events[-1] == {"done": True}
    shown = next(e for e in events if "tool_result" in e)["tool_result"]["result"]
    assert JPEG not in shown


def test_a_failed_look_gives_the_model_no_picture_and_the_error_to_repeat(monkeypatch):
    scripted, events = collect_loop(monkeypatch, "https://api.openai.com/v1", looking(), {TOOL}, failure("declined"))
    second_round = scripted.seen[1]
    assert second_round[-1]["role"] == "tool" and second_round[-1]["content"].startswith("error: could not look")
    assert all(not isinstance(m.get("content"), list) for m in second_round)


def test_the_tool_is_not_run_when_it_was_not_offered(monkeypatch):
    scripted, events = collect_loop(monkeypatch, "http://model", looking(), set(), picture())
    shown = next(e for e in events if "tool_result" in e)["tool_result"]["result"]
    assert shown.startswith("error: the tool look_at_screen is not enabled for this chat")
    assert not any("screen_request" in e for e in events)


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
    hashed = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    users.upsert("admin@example.com", "Admin", "admin", hashed)
    users.upsert("second@example.com", "Second", "admin", hashed)
    users.upsert("sam@example.com", "Sam", "user", hashed)
    application = FastAPI()
    auth.install(application, users, sessions)
    application.include_router(auth_routes.router)
    application.include_router(routes.router)
    return application


def login(app, email):
    client = TestClient(app, base_url="https://testserver", headers=HEADERS)
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    client.user_id = auth_routes.users.find_by_email(email).id
    return client


@pytest.fixture
def offered(monkeypatch):
    seen = []

    async def with_tools(base_url, api_key, model, messages, system, options, servers, tools, ctx, max_tools=None):
        seen.append((set(tools), ctx))
        yield chat_stream._sse_line({"done": True})

    async def without_tools(base_url, api_key, model, messages, system, options):
        seen.append((None, None))
        yield chat_stream._sse_line({"done": True})

    monkeypatch.setattr(routes, "run_chat_with_tools", with_tools)
    monkeypatch.setattr(routes, "stream_chat", without_tools)
    return seen


@pytest.fixture
def endpoint():
    return routes.endpoints.add("https://models.example.com/v1", kind="api-key")


def chat(client, endpoint_id, tools=(), **extra):
    return client.post("/api/chat_stream", json={
        "endpoint_id": endpoint_id, "model": "gpt-4o", "messages": [{"role": "user", "content": "look at my discord"}],
        "enabled_builtin_tools": list(tools), **extra})


def test_an_admin_in_starfire_desktop_with_the_link_connected_is_offered_the_tool(app, offered, endpoint):
    chat(login(app, "admin@example.com"), endpoint.id, desktop_screen=True)
    tools, ctx = offered[0]
    assert tools == {TOOL} and ctx.desktop_screen is True and ctx.may_use_host_tools is True


def test_other_tools_the_user_enabled_stay_alongside(app, offered, endpoint):
    chat(login(app, "admin@example.com"), endpoint.id, tools=["manage_notes"], desktop_screen=True)
    assert offered[0][0] == {TOOL, "manage_notes"}


def test_a_normal_browser_is_never_offered_the_tool_even_if_it_names_it(app, offered, endpoint):
    admin = login(app, "admin@example.com")
    chat(admin, endpoint.id, tools=[TOOL, "manage_notes"])
    chat(admin, endpoint.id, tools=[TOOL])
    assert offered[0][0] == {"manage_notes"}
    assert offered[0][1].desktop_screen is False
    assert offered[1][0] is None


def test_an_ordinary_account_is_never_offered_the_tool(app, offered, endpoint):
    sam = login(app, "sam@example.com")
    chat(sam, endpoint.id, tools=[TOOL], desktop_screen=True)
    chat(sam, endpoint.id, tools=["manage_notes", TOOL], desktop_screen=True)
    assert offered[0][0] is None
    assert offered[1][0] == {"manage_notes"} and offered[1][1].desktop_screen is False


def test_a_disabled_admin_is_not_offered_the_tool(app, offered, endpoint):
    admin = login(app, "admin@example.com")
    auth_routes.users.update(admin.user_id, role="user")
    chat(admin, endpoint.id, desktop_screen=True)
    assert offered == [(None, None)]


def test_a_scheduled_task_never_gets_the_tool(monkeypatch):
    seen = {}

    async def collected(*args, **kwargs):
        seen.update(kwargs)
        return "done"

    monkeypatch.setattr(routes, "endpoints", SimpleNamespace(get=lambda endpoint_id: SimpleNamespace(
        id="e", provider="openai", base_url="https://models.example.com/v1", label="")))
    monkeypatch.setattr(routes, "_api_key_for", lambda endpoint_id, provider: None)
    monkeypatch.setattr(routes, "approval_policy", SimpleNamespace(get=lambda: routes.ApprovalPolicy()))
    monkeypatch.setattr(routes, "_check_scheduled_generation", lambda model: None)
    monkeypatch.setattr(routes, "_tool_context", lambda **kwargs: looking())
    monkeypatch.setattr(agent_loop, "run_chat_collected", collected)
    asyncio.run(routes._task_chat_fn("e", "gpt-4o", "go", [], [TOOL, "manage_notes"]))
    assert seen["enabled_builtin_tools"] == {"manage_notes"}



def task_model_that_offers(offered_runs):
    async def model(base_url, api_key, model, messages, system, options, tools=None, think=None):
        offered = [tool["function"]["name"] for tool in (tools or [])]
        offered_runs.append(offered)
        if TOOL in offered and not any(message["role"] == "tool" for message in messages):
            yield "tool_calls", [{"id": "c1", "name": TOOL, "arguments": {"app": ""}}]
            return
        yield "delta", "done"
    return model


def test_a_task_run_now_from_a_desktop_chat_is_never_offered_the_tool(monkeypatch):
    from task_store import ScheduledTask

    offered_runs = []
    monkeypatch.setattr(agent_loop, "stream_chat_once", task_model_that_offers(offered_runs))
    monkeypatch.setattr(screen_look, "LOOK_TIMEOUT_SECONDS", 1)
    task = ScheduledTask(id="t1", name="morning look", prompt="look at my screen", schedule="daily",
                         enabled_builtin_tools=[TOOL, "manage_notes"], model="m")
    runs = {}

    def add_run(run):
        run.id = "r1"
        runs[run.id] = run
        return run

    def update_run(run_id, **fields):
        for key, value in fields.items():
            setattr(runs[run_id], key, value)

    ctx = looking()
    vars(ctx).update(base_url="https://models.example.com/v1", api_key=None, model="m", endpoint_id="e",
                     tasks=SimpleNamespace(get=lambda task_id: task, update=lambda task_id, **fields: None),
                     task_runs=SimpleNamespace(add=add_run, update=update_run))
    result = asyncio.run(builtin_tools.call_builtin_tool("manage_tasks", {"action": "run_now", "task_id": "t1"}, ctx))
    assert result == "ran"
    assert offered_runs and all(TOOL not in offered for offered in offered_runs)
    assert screen_look._pending == {}


def test_no_collected_run_offers_the_tool_whatever_it_is_handed(monkeypatch):
    offered_runs = []
    monkeypatch.setattr(agent_loop, "stream_chat_once", task_model_that_offers(offered_runs))
    monkeypatch.setattr(screen_look, "LOOK_TIMEOUT_SECONDS", 1)
    answer = asyncio.run(agent_loop.run_chat_collected(
        "https://models.example.com/v1", None, "m", [{"role": "user", "content": "look"}],
        enabled_builtin_tools={TOOL, "manage_notes"}, ctx=looking()))
    assert answer == "done"
    assert offered_runs and all(TOOL not in offered for offered in offered_runs)


def stage_for(client, app="Discord"):
    holder = {}

    async def go():
        holder["id"] = screen_look.stage(app, client.user_id)
    asyncio.run(go())
    return holder["id"]


def test_the_page_claims_then_answers_its_own_request(app):
    admin = login(app, "admin@example.com")
    request_id = stage_for(admin)
    claimed = admin.post(f"/api/screen-looks/{request_id}/claim")
    assert claimed.status_code == 200 and claimed.json() == {"app": "Discord"}
    assert admin.post(f"/api/screen-looks/{request_id}/claim").status_code == 409
    answered_ = admin.post(f"/api/screen-looks/{request_id}/result", json=picture())
    assert answered_.status_code == 200
    assert admin.post(f"/api/screen-looks/{request_id}/result", json=picture()).status_code == 404


def test_another_account_cannot_claim_or_answer(app):
    admin, second = login(app, "admin@example.com"), login(app, "second@example.com")
    request_id = stage_for(admin)
    assert second.post(f"/api/screen-looks/{request_id}/claim").status_code == 404
    assert second.post(f"/api/screen-looks/{request_id}/result", json=picture()).status_code == 404
    assert admin.post(f"/api/screen-looks/{request_id}/claim").status_code == 200


def test_an_account_that_is_no_longer_an_admin_cannot_claim_or_answer(app):
    admin = login(app, "admin@example.com")
    request_id = stage_for(admin)
    auth_routes.users.update(admin.user_id, role="user")
    assert admin.post(f"/api/screen-looks/{request_id}/claim").status_code == 403
    assert admin.post(f"/api/screen-looks/{request_id}/result", json=picture()).status_code == 403


def test_a_signed_out_caller_cannot_touch_a_request(app):
    admin = login(app, "admin@example.com")
    request_id = stage_for(admin)
    anonymous = TestClient(app, base_url="https://testserver", headers=HEADERS)
    assert anonymous.post(f"/api/screen-looks/{request_id}/claim").status_code == 401
    assert anonymous.post(f"/api/screen-looks/{request_id}/result", json=picture()).status_code == 401


def test_a_bad_picture_is_a_400_and_the_request_stays_open_for_a_failure(app):
    admin = login(app, "admin@example.com")
    request_id = stage_for(admin)
    admin.post(f"/api/screen-looks/{request_id}/claim")
    assert admin.post(f"/api/screen-looks/{request_id}/result", json=picture(mime="text/html")).status_code == 400
    assert admin.post(f"/api/screen-looks/{request_id}/result", json=failure("failed", "bad picture")).status_code == 200


def test_an_answer_cannot_carry_extra_fields_or_huge_text(app):
    admin = login(app, "admin@example.com")
    request_id = stage_for(admin)
    assert admin.post(f"/api/screen-looks/{request_id}/result", json={**failure("failed"), "command": "id"}).status_code == 422
    assert admin.post(f"/api/screen-looks/{request_id}/result", json={**failure("failed"), "detail": "x" * 5000}).status_code == 422


def test_the_model_is_told_a_named_app_is_looked_at_through_its_window_only():
    schema = next(tool for tool in builtin_tools.TOOL_SCHEMAS if tool["function"]["name"] == builtin_tools.SCREEN_TOOL)
    description = schema["function"]["description"]
    assert "that app's window" in description
    assert "it takes a screenshot of their whole screen and" not in description
