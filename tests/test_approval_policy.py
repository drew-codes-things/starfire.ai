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
import builtin_tools
import chat_stream
import routes
import shell_tool
import user_data
from api_key_manager import APIKeyManager
from auth import SessionStore, UserStore
from config import config
from model_endpoints import ModelEndpointStore

pytestmark = pytest.mark.usefixtures("unreadable_model_lists")

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}


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
    return client


@pytest.fixture
def seen_contexts(monkeypatch):
    contexts = []

    async def with_tools(base_url, api_key, model, messages, system, options, servers, tools, ctx, max_tools=None):
        contexts.append(ctx)
        yield chat_stream._sse_line({"done": True})

    monkeypatch.setattr(routes, "run_chat_with_tools", with_tools)
    return contexts


@pytest.fixture
def endpoint():
    return routes.endpoints.add("https://models.example.com/v1", kind="api-key")


def chat(client, endpoint_id, **flags):
    return client.post("/api/chat_stream", json={
        "endpoint_id": endpoint_id, "model": "gpt-4o", "messages": [{"role": "user", "content": "tidy up"}],
        "enabled_builtin_tools": ["run_shell", "edit_file"], **flags})


def approvals(ctx):
    return ctx.require_shell_approval, ctx.require_edit_approval


def test_a_direct_post_cannot_switch_approval_off(app, seen_contexts, endpoint):
    admin = login(app, "admin@example.com")
    chat(admin, endpoint.id, require_shell_approval=False, require_edit_approval=False)
    assert [approvals(ctx) for ctx in seen_contexts] == [(True, True)]


def test_every_account_starts_with_shell_and_edit_approval_on(app):
    for email in ("admin@example.com", "sam@example.com"):
        assert login(app, email).get("/api/approval-policy").json() == {
            "require_shell_approval": True, "require_edit_approval": True}


def test_an_admin_turns_approval_off_on_the_server_and_a_request_can_only_turn_it_back_on(app, seen_contexts, endpoint):
    admin = login(app, "admin@example.com")
    stored = admin.put("/api/approval-policy", json={"require_shell_approval": False, "require_edit_approval": False})
    assert stored.json() == {"require_shell_approval": False, "require_edit_approval": False}
    chat(admin, endpoint.id)
    chat(admin, endpoint.id, require_shell_approval=True)
    chat(admin, endpoint.id, require_edit_approval=True, require_shell_approval=False)
    assert [approvals(ctx) for ctx in seen_contexts] == [(False, False), (True, False), (False, True)]


def test_a_partial_change_keeps_the_other_setting(app):
    admin = login(app, "admin@example.com")
    admin.put("/api/approval-policy", json={"require_edit_approval": False})
    assert admin.get("/api/approval-policy").json() == {"require_shell_approval": True, "require_edit_approval": False}


def test_an_ordinary_account_cannot_change_the_policy(app):
    sam = login(app, "sam@example.com")
    refused = sam.put("/api/approval-policy", json={"require_shell_approval": False})
    assert refused.status_code == 403
    assert sam.get("/api/approval-policy").json()["require_shell_approval"] is True


def test_each_account_keeps_its_own_policy(app, seen_contexts, endpoint):
    admin, second = login(app, "admin@example.com"), login(app, "second@example.com")
    admin.put("/api/approval-policy", json={"require_shell_approval": False})
    assert second.get("/api/approval-policy").json()["require_shell_approval"] is True
    chat(second, endpoint.id)
    assert [approvals(ctx) for ctx in seen_contexts] == [(True, True)]


def test_a_signed_out_request_cannot_change_the_policy(app):
    anonymous = TestClient(app, base_url="https://testserver", headers=HEADERS)
    assert anonymous.put("/api/approval-policy", json={"require_shell_approval": False}).status_code == 401


def test_an_unreadable_policy_file_means_both_approvals_stay_on(tmp_path):
    from approval_policy import ApprovalPolicyStore

    (tmp_path / "approval_policy.json").write_text("{not json")
    policy = ApprovalPolicyStore(str(tmp_path)).get()
    assert (policy.require_shell_approval, policy.require_edit_approval) == (True, True)


def test_a_scheduled_task_runs_with_the_owners_stored_policy(app, endpoint, monkeypatch):
    admin = login(app, "admin@example.com")
    admin_id = admin.get("/api/auth/me").json()["id"]
    contexts = []

    async def collected(*args, ctx=None, **kwargs):
        contexts.append(ctx)
        return "ok"

    monkeypatch.setattr("agent_loop.run_chat_collected", collected)
    with user_data.run_as(admin_id):
        asyncio.run(routes._task_chat_fn(endpoint.id, "gpt-4o", "clean up", [], ["run_shell"]))
    admin.put("/api/approval-policy", json={"require_shell_approval": False})
    with user_data.run_as(admin_id):
        asyncio.run(routes._task_chat_fn(endpoint.id, "gpt-4o", "clean up", [], ["run_shell"]))
    assert [ctx.require_shell_approval for ctx in contexts] == [True, False]


def test_a_command_that_needs_approval_in_a_scheduled_task_is_refused_at_once(tmp_path):
    marker = tmp_path / "ran"
    ctx = builtin_tools.ToolContext(memory=None, documents=None, tasks=None, task_runs=None, email_accounts=None,
                                    api_keys=None, notes=None, may_use_host_tools=True)
    result = asyncio.run(builtin_tools._run_shell({"command": f"touch {marker}"}, ctx))
    assert result == builtin_tools.NO_ONE_TO_ASK
    assert not marker.exists() and shell_tool.list_pending(None) == []


def tool_call_reply(command):
    call = {"index": 0, "id": "call-1", "function": {"name": "run_shell", "arguments": json.dumps({"command": command})}}
    return "\n\n".join([
        "data: " + json.dumps({"choices": [{"delta": {"tool_calls": [call]}}]}),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]", ""])


def test_a_direct_post_asking_for_no_shell_approval_still_gets_a_card_and_nothing_runs(app, endpoint, tmp_path,
                                                                                       monkeypatch):
    marker = tmp_path / "ran-without-approval"
    replies = iter([tool_call_reply(f"touch {marker}"),
                    'data: {"choices": [{"delta": {"content": "ok"}}]}\n\ndata: [DONE]\n\n'])
    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text=next(replies)))
        return real_client(*args, transport=transport, **kwargs)

    monkeypatch.setattr(chat_stream.httpx, "AsyncClient", client)
    monkeypatch.setattr(shell_tool, "APPROVAL_TIMEOUT_SECONDS", 0.2)
    admin = login(app, "admin@example.com")
    reply = chat(admin, endpoint.id, require_shell_approval=False, enabled_builtin_tools=["run_shell"])
    events = [json.loads(line[len("data: "):]) for line in reply.text.split("\n\n") if line.startswith("data: ")]
    assert any("shell_pending" in event for event in events)
    assert not marker.exists()


def read_static(name):
    with open(os.path.join(ROOT, "static", name), encoding="utf-8") as f:
        return f.read()


def test_the_page_no_longer_sends_its_own_approval_choice_with_a_chat():
    sender = read_static("chatSend.js")
    assert "require_shell_approval" not in sender and "require_edit_approval" not in sender


def test_the_approval_toggles_read_and_write_the_server_policy_and_drop_the_old_browser_copies():
    settings = read_static("settings.js")
    assert "localStorage.setItem('sf_edit_approval'" not in settings
    assert "localStorage.setItem('sf_shell_approval'" not in settings
    assert "localStorage.removeItem(key)" in settings and "'sf_edit_approval', 'sf_shell_approval'" in settings
    assert "fetch('/api/approval-policy')" in settings
    assert "fetch('/api/approval-policy', {\n      method: 'PUT'" in settings
