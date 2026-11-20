import asyncio
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
import browser_open
import builtin_tools
import chat_stream
import model_capabilities
import routes
import shell_tool
import user_data
from api_key_manager import APIKeyManager
from auth import SessionStore, UserStore
from config import config
from model_endpoints import ModelEndpointStore

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}
TOOL = "open_in_browser"
SCREEN = "look_at_screen"
ADDRESS = "https://example.org/docs?page=2"


@pytest.fixture(autouse=True)
def nothing_left_waiting():
    yield
    browser_open._pending.clear()


class Context(SimpleNamespace):
    async def emit(self, event):
        if self.events is not None:
            await self.events.put(event)


def opening(admin=True, desktop=True, with_events=True):
    return Context(may_use_host_tools=admin, desktop_browser=desktop, desktop_screen=False,
                   events=asyncio.Queue() if with_events else None, screen_images=[], untrusted_read=False)


async def answered(ctx, outcome, owner="owner"):
    request = await asyncio.wait_for(ctx.events.get(), 5)
    request_id = request["browser_request"]["request_id"]
    browser_open.claim(request_id, owner)
    browser_open.resolve(request_id, owner, outcome)
    return request["browser_request"]


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


def opened(**fields):
    return {"ok": True, "browser": "Firefox", "tab": 12, **fields}


def failure(reason, detail=""):
    return {"ok": False, "reason": reason, "detail": detail}


def test_the_tool_is_in_the_catalogue_asks_for_itself_and_reads_nothing():
    assert TOOL in {schema["function"]["name"] for schema in builtin_tools.TOOL_SCHEMAS}
    assert TOOL in builtin_tools.TOOLS_THAT_ASK_FOR_THEMSELVES
    assert TOOL in builtin_tools.DESKTOP_TOOLS and SCREEN in builtin_tools.DESKTOP_TOOLS
    assert not builtin_tools.reads_untrusted_content(TOOL, {})
    assert TOOL in model_capabilities.BASIC_TIER_TOOLS
    schema = next(tool for tool in builtin_tools.TOOL_SCHEMAS if tool["function"]["name"] == TOOL)
    assert schema["function"]["parameters"]["required"] == ["url"]
    assert set(schema["function"]["parameters"]["properties"]) == {"url", "browser"}


def test_an_open_asks_the_page_first_and_tells_the_model_nothing_was_read():
    ctx = opening()
    result, request = run_tool(ctx, {"url": ADDRESS, "browser": "Firefox"}, opened())
    assert request["url"] == ADDRESS and request["browser"] == "Firefox" and len(request["request_id"]) >= 12
    assert result.startswith(f"Opened {ADDRESS} in Firefox, tab 12.")
    assert "Nothing from the page was read" in result
    assert ctx.screen_images == []


def test_no_browser_named_is_an_empty_browser_and_the_answer_names_the_one_used():
    result, request = run_tool(opening(), {"url": ADDRESS}, opened(browser="Chrome 2", tab=None))
    assert request["browser"] == ""
    assert result.startswith(f"Opened {ADDRESS} in Chrome 2.")


@pytest.mark.parametrize("reason,words", [
    ("declined", "said no"),
    ("timed_out", "in time"),
    ("abandoned", "dropped"),
    ("busy", "already waiting"),
    ("bad_request", "refused"),
    ("no_browser", "no browser"),
    ("which_browser", "which browser"),
    ("not_connected", "not connected"),
    ("unavailable", "not available"),
    ("not_running", "not running"),
    ("not_linked", "not linked"),
    ("failed", "failed"),
])
def test_every_way_it_can_fail_tells_the_model_to_say_so_and_not_to_guess(reason, words):
    result, _request = run_tool(opening(), {"url": ADDRESS}, failure(reason))
    assert result.startswith(f"error: could not open {ADDRESS}")
    assert words in result
    assert "Tell the user plainly that you could not open it" in result
    assert "Do not guess" in result


def test_an_unknown_reason_still_fails_honestly():
    result, _request = run_tool(opening(), {"url": ADDRESS}, failure("something odd"))
    assert result.startswith("error: could not open") and "Nothing was opened" in result


def test_what_the_page_says_went_wrong_is_shown_cleaned_and_short():
    detail = "Firefox is not connected.‮" + "x" * 1000
    result, _request = run_tool(opening(), {"url": ADDRESS}, failure("not_connected", detail))
    assert "Firefox is not connected." in result and "‮" not in result
    assert len(result) < 900


def test_an_ordinary_account_cannot_open():
    result, _request = run_tool(opening(admin=False), {"url": ADDRESS}, None)
    assert result.startswith("error: only an admin account can use open_in_browser")
    assert browser_open._pending == {}


def test_a_chat_that_did_not_come_from_starfire_desktop_cannot_open():
    result, _request = run_tool(opening(desktop=False), {"url": ADDRESS}, None)
    assert result.startswith("error: open_in_browser is only available in starfire Desktop")
    assert browser_open._pending == {}


def test_a_task_with_no_one_to_ask_cannot_open():
    result, _request = run_tool(opening(with_events=False), {"url": ADDRESS}, None)
    assert result == builtin_tools.NO_ONE_TO_ASK
    assert browser_open._pending == {}


@pytest.mark.parametrize("arguments", [
    {}, {"url": ""}, {"url": 5}, {"url": [ADDRESS]}, {"url": "example.org"}, {"url": "javascript:alert(1)"},
    {"url": "JAVASCRIPT:alert(1)"}, {"url": "data:text/html,hi"}, {"url": "file:///etc/passwd"}, {"url": "ftp://example.org/"},
    {"url": "https://"}, {"url": "https://example.org/" + "a" * 2000}, {"url": "https://exam‮ple.org/"},
    {"url": "https://example.org/\npath"}, {"url": "https://example.org/ path"}, {"url": " https://example.org/"},
    {"url": "https://exam​ple.org/"}, {"url": "https://example.org/\x00"},
])
def test_a_bad_address_is_refused_before_anything_is_staged(arguments):
    ctx = opening()
    result, _request = run_tool(ctx, arguments, None)
    assert result.startswith("error: ") and "url" in result
    assert browser_open._pending == {}
    assert ctx.events.empty()


@pytest.mark.parametrize("url", ["https://accounts.google.com." + "a" * 1900 + ".example/", "https://" + "a" * 64 + ".example/",
                                 "https://" + ".".join(["a" * 60] * 5) + ".example/"],
                         ids=["very long host", "label over 63", "host over 253"])
def test_a_site_too_long_to_show_on_the_card_is_refused_before_anything_is_staged(url, monkeypatch):
    monkeypatch.setattr(browser_open, "OPEN_TIMEOUT_SECONDS", 0.1)
    ctx = opening()
    result, _request = run_tool(ctx, {"url": url}, None)
    assert result.startswith("error: ") and "site" in result
    assert browser_open._pending == {}


@pytest.mark.parametrize("browser", [5, None, ["Firefox"], "Netscape", "firefox", "Firefox 1", "Firefox 10", "Fire‮fox",
                                     "Firefox\n", "x" * 41, " Firefox"])
def test_a_bad_browser_is_refused_before_anything_is_staged(browser):
    ctx = opening()
    result, _request = run_tool(ctx, {"url": ADDRESS, "browser": browser}, None)
    assert result.startswith("error: ") and "browser" in result
    assert browser_open._pending == {}
    assert ctx.events.empty()


def test_an_address_exactly_at_the_limit_is_staged():
    url = "https://example.org/" + "a" * (2000 - len("https://example.org/"))
    _result, request = run_tool(opening(), {"url": url, "browser": "Edge 2"}, opened())
    assert request["url"] == url and request["browser"] == "Edge 2"


def test_the_wait_ends_with_a_timeout_failure_when_nobody_answers(monkeypatch):
    monkeypatch.setattr(browser_open, "OPEN_TIMEOUT_SECONDS", 0.05)
    result, _request = run_tool(opening(), {"url": ADDRESS}, None)
    assert result.startswith(f"error: could not open {ADDRESS}") and "in time" in result
    assert browser_open._pending == {}


def test_a_request_can_be_claimed_once_by_its_owner():
    async def go():
        request_id = browser_open.stage(ADDRESS, "Firefox", "owner")
        with pytest.raises(browser_open.NotWaiting):
            browser_open.claim(request_id, "someone else")
        assert browser_open.claim(request_id, "owner") == {"url": ADDRESS, "browser": "Firefox"}
        with pytest.raises(browser_open.AlreadyClaimed):
            browser_open.claim(request_id, "owner")
        with pytest.raises(browser_open.NotWaiting):
            browser_open.claim("nothing", "owner")
    asyncio.run(go())


def test_only_the_owner_can_answer_and_only_once():
    async def go():
        request_id = browser_open.stage(ADDRESS, "", "owner")
        assert browser_open.resolve(request_id, "someone else", opened()) is False
        assert browser_open.resolve(request_id, "owner", opened()) is True
        assert browser_open.resolve(request_id, "owner", opened()) is False
    asyncio.run(go())


@pytest.mark.parametrize("outcome", [opened(browser="Netscape"), opened(browser="Fire‮fox"), opened(tab=-1)])
def test_an_open_that_names_an_unknown_browser_or_tab_is_refused(outcome):
    async def go():
        request_id = browser_open.stage(ADDRESS, "", "owner")
        with pytest.raises(browser_open.BadAnswer):
            browser_open.resolve(request_id, "owner", outcome)
        assert browser_open.resolve(request_id, "owner", failure("failed")) is True
    asyncio.run(go())


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


def collect_loop(monkeypatch, ctx, enabled, outcome):
    scripted = Scripted([("tool_calls", [{"id": "c1", "name": TOOL, "arguments": {"url": ADDRESS}}])], [("delta", "Opened it.")])
    monkeypatch.setattr(agent_loop, "stream_chat_once", scripted)

    async def go():
        token = user_data.current_user_id.set("owner")
        try:
            chunks = []
            async for chunk in agent_loop.run_chat_with_tools("http://model", None, "m", [{"role": "user", "content": "open it"}],
                                                              None, None, set(), enabled, ctx):
                chunks.append(json.loads(chunk.decode()[len("data:"):]))
                if "browser_request" in chunks[-1]:
                    request_id = chunks[-1]["browser_request"]["request_id"]
                    browser_open.claim(request_id, "owner")
                    browser_open.resolve(request_id, "owner", outcome)
            return chunks
        finally:
            user_data.current_user_id.reset(token)
    return scripted, asyncio.run(go())


def test_the_loop_gives_the_model_the_result_and_does_not_mark_the_chat_untrusted(monkeypatch):
    scripted, events = collect_loop(monkeypatch, opening(), {TOOL}, opened())
    assert scripted.tools[0] == [TOOL]
    assert scripted.seen[1][-1]["role"] == "tool" and scripted.seen[1][-1]["content"].startswith("Opened")
    assert {"untrusted_read": True} not in events
    assert events[-1] == {"done": True}


def test_the_tool_is_not_run_when_it_was_not_offered(monkeypatch):
    _scripted, events = collect_loop(monkeypatch, opening(), set(), opened())
    shown = next(e for e in events if "tool_result" in e)["tool_result"]["result"]
    assert shown.startswith("error: the tool open_in_browser is not enabled for this chat")
    assert not any("browser_request" in e for e in events)


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
        "endpoint_id": endpoint_id, "model": "gpt-4o", "messages": [{"role": "user", "content": "open example.org"}],
        "enabled_builtin_tools": list(tools), **extra})


def test_an_admin_in_starfire_desktop_with_browser_open_available_is_offered_the_tool(app, offered, endpoint):
    chat(login(app, "admin@example.com"), endpoint.id, desktop_browser=True)
    tools, ctx = offered[0]
    assert tools == {TOOL} and ctx.desktop_browser is True and ctx.desktop_screen is False and ctx.may_use_host_tools is True


def test_screen_and_browser_are_offered_on_their_own_flags(app, offered, endpoint):
    admin = login(app, "admin@example.com")
    chat(admin, endpoint.id, desktop_screen=True)
    chat(admin, endpoint.id, desktop_screen=True, desktop_browser=True, tools=["manage_notes"])
    assert offered[0][0] == {SCREEN} and offered[0][1].desktop_browser is False
    assert offered[1][0] == {SCREEN, TOOL, "manage_notes"}


def test_without_the_flag_the_tool_is_never_offered_even_if_named(app, offered, endpoint):
    admin = login(app, "admin@example.com")
    chat(admin, endpoint.id, tools=[TOOL, "manage_notes"])
    chat(admin, endpoint.id, tools=[TOOL])
    assert offered[0][0] == {"manage_notes"} and offered[0][1].desktop_browser is False
    assert offered[1][0] is None


def test_an_ordinary_account_is_never_offered_the_tool(app, offered, endpoint):
    sam = login(app, "sam@example.com")
    chat(sam, endpoint.id, tools=[TOOL], desktop_browser=True)
    chat(sam, endpoint.id, tools=["manage_notes", TOOL], desktop_browser=True)
    assert offered[0][0] is None
    assert offered[1][0] == {"manage_notes"} and offered[1][1].desktop_browser is False


def test_an_admin_that_was_demoted_is_not_offered_the_tool(app, offered, endpoint):
    admin = login(app, "admin@example.com")
    auth_routes.users.update(admin.user_id, role="user")
    chat(admin, endpoint.id, desktop_browser=True)
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
    monkeypatch.setattr(routes, "_tool_context", lambda **kwargs: opening())
    monkeypatch.setattr(agent_loop, "run_chat_collected", collected)
    asyncio.run(routes._task_chat_fn("e", "gpt-4o", "go", [], [TOOL, SCREEN, "manage_notes"]))
    assert seen["enabled_builtin_tools"] == {"manage_notes"}


def test_no_collected_run_offers_the_tool_whatever_it_is_handed(monkeypatch):
    offered_runs = []

    async def model(base_url, api_key, model, messages, system, options, tools=None, think=None):
        offered_runs.append([tool["function"]["name"] for tool in (tools or [])])
        yield "delta", "done"

    monkeypatch.setattr(agent_loop, "stream_chat_once", model)
    answer = asyncio.run(agent_loop.run_chat_collected(
        "https://models.example.com/v1", None, "m", [{"role": "user", "content": "open"}],
        enabled_builtin_tools={TOOL, "manage_notes"}, ctx=opening()))
    assert answer == "done"
    assert offered_runs and all(TOOL not in offered for offered in offered_runs)


def stage_for(client, url=ADDRESS, browser="Firefox"):
    holder = {}

    async def go():
        holder["id"] = browser_open.stage(url, browser, client.user_id)
    asyncio.run(go())
    return holder["id"]


def test_the_page_claims_then_answers_its_own_request(app):
    admin = login(app, "admin@example.com")
    request_id = stage_for(admin)
    claimed = admin.post(f"/api/browser-opens/{request_id}/claim")
    assert claimed.status_code == 200 and claimed.json() == {"url": ADDRESS, "browser": "Firefox"}
    assert admin.post(f"/api/browser-opens/{request_id}/claim").status_code == 409
    assert admin.post(f"/api/browser-opens/{request_id}/result", json=opened()).status_code == 200
    assert admin.post(f"/api/browser-opens/{request_id}/result", json=opened()).status_code == 404


def test_another_account_cannot_claim_or_answer(app):
    admin, second = login(app, "admin@example.com"), login(app, "second@example.com")
    request_id = stage_for(admin)
    assert second.post(f"/api/browser-opens/{request_id}/claim").status_code == 404
    assert second.post(f"/api/browser-opens/{request_id}/result", json=opened()).status_code == 404
    assert admin.post(f"/api/browser-opens/{request_id}/claim").status_code == 200


def test_an_account_that_is_no_longer_an_admin_cannot_claim_or_answer(app):
    admin = login(app, "admin@example.com")
    request_id = stage_for(admin)
    auth_routes.users.update(admin.user_id, role="user")
    assert admin.post(f"/api/browser-opens/{request_id}/claim").status_code == 403
    assert admin.post(f"/api/browser-opens/{request_id}/result", json=opened()).status_code == 403


def test_a_signed_out_caller_cannot_touch_a_request(app):
    admin = login(app, "admin@example.com")
    request_id = stage_for(admin)
    anonymous = TestClient(app, base_url="https://testserver", headers=HEADERS)
    assert anonymous.post(f"/api/browser-opens/{request_id}/claim").status_code == 401
    assert anonymous.post(f"/api/browser-opens/{request_id}/result", json=opened()).status_code == 401


def test_an_unknown_browser_is_a_400_and_the_request_stays_open_for_a_failure(app):
    admin = login(app, "admin@example.com")
    request_id = stage_for(admin)
    admin.post(f"/api/browser-opens/{request_id}/claim")
    assert admin.post(f"/api/browser-opens/{request_id}/result", json=opened(browser="Netscape")).status_code == 400
    assert admin.post(f"/api/browser-opens/{request_id}/result", json=failure("failed", "bad answer")).status_code == 200


@pytest.mark.parametrize("body", [
    {**failure("failed"), "command": "id"},
    {**failure("failed"), "page_text": "hello"},
    {**failure("failed"), "detail": "x" * 5000},
    {**failure("x" * 41)},
    opened(browser="x" * 41),
    opened(tab="twelve"),
    {"browser": "Firefox", "tab": 1},
])
def test_an_answer_cannot_carry_extra_fields_or_oversized_text(app, body):
    admin = login(app, "admin@example.com")
    request_id = stage_for(admin)
    assert admin.post(f"/api/browser-opens/{request_id}/result", json=body).status_code == 422


def run_after_untrusted_content(approved, outcome):
    async def go():
        token = user_data.current_user_id.set("owner")
        ctx = opening()
        ctx.untrusted_read = True
        try:
            task = asyncio.ensure_future(builtin_tools.call_builtin_tool(TOOL, {"url": ADDRESS}, ctx))
            asked = (await asyncio.wait_for(ctx.events.get(), 5))["shell_pending"]
            assert browser_open._pending == {}
            shell_tool.resolve(asked["pending_id"], approved, "owner")
            seen = await answered(ctx, outcome) if approved else None
            return await task, asked, seen
        finally:
            user_data.current_user_id.reset(token)
    return asyncio.run(go())


def test_after_untrusted_content_starfire_asks_with_its_warning_before_arty_is_asked():
    result, asked, seen = run_after_untrusted_content(True, opened())
    assert asked["tool"] == TOOL and asked["untrusted"] is True and ADDRESS in asked["command"]
    assert seen["url"] == ADDRESS and result.startswith(f"Opened {ADDRESS}")


def test_after_untrusted_content_a_rejected_open_never_reaches_arty():
    result, _asked, _seen = run_after_untrusted_content(False, None)
    assert result.startswith("error: the user rejected") and browser_open._pending == {}
