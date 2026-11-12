import asyncio
import json
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import pytest

import builtin_tools
import file_edit_tool
import routes
import shell_tool
import user_data


def context(may_use_host_tools: bool):
    async def emit(event):
        return None

    return SimpleNamespace(may_use_host_tools=may_use_host_tools, require_edit_approval=False,
                           require_shell_approval=False, events=None, emit=emit)


@pytest.mark.parametrize("name, arguments", [
    ("run_shell", {"command": "echo should-not-run"}),
    ("edit_file", {"path": "PLACEHOLDER", "content": "written"}),
    ("github_cli", {"args": ["auth", "status"]}),
])
def test_accounts_that_are_not_admins_cannot_use_host_tools(tmp_path, name, arguments):
    target = tmp_path / "untouched.txt"
    if name == "edit_file":
        arguments = {**arguments, "path": str(target)}
    result = asyncio.run(builtin_tools.call_builtin_tool(name, arguments, context(False)))
    assert result.startswith("error: only an admin account can use")
    assert "should-not-run" not in result
    assert not target.exists()


def test_admins_can_use_host_tools(tmp_path):
    target = tmp_path / "written.txt"
    ctx = context(True)
    asyncio.run(builtin_tools.call_builtin_tool("edit_file", {"path": str(target), "content": "hello"}, ctx))
    assert target.read_text() == "hello"
    result = asyncio.run(builtin_tools.call_builtin_tool("run_shell", {"command": "echo admin-ran"}, ctx))
    assert "admin-ran" in result


def test_pending_shell_commands_belong_to_the_account_that_started_them():
    async def run():
        pending_id = shell_tool.stage("echo mine", None, owner="alice")
        assert [entry["id"] for entry in shell_tool.list_pending("alice")] == [pending_id]
        assert shell_tool.list_pending("bob") == []
        assert not shell_tool.resolve(pending_id, True, owner="bob")
        assert shell_tool.resolve(pending_id, False, owner="alice")
        assert await shell_tool.wait_for_decision(pending_id) is False

    asyncio.run(run())


def test_pending_edits_belong_to_the_account_that_started_them():
    edit_id = file_edit_tool.stage("/tmp/x", "content", "diff", owner="alice")
    try:
        assert [entry["id"] for entry in file_edit_tool.list_pending("alice")] == [edit_id]
        assert file_edit_tool.list_pending("bob") == []
        assert file_edit_tool.get_pending(edit_id, owner="bob") is None
        assert not file_edit_tool.remove_pending(edit_id, owner="bob")
        assert file_edit_tool.get_pending(edit_id, owner="alice")["path"] == "/tmp/x"
    finally:
        assert file_edit_tool.remove_pending(edit_id, owner="alice")


def test_staged_edit_is_owned_by_the_current_account(tmp_path):
    ctx = SimpleNamespace(may_use_host_tools=True, require_edit_approval=True)
    with user_data.run_as("alice"):
        staged = asyncio.run(builtin_tools.call_builtin_tool(
            "edit_file", {"path": str(tmp_path / "f.txt"), "content": "x"}, ctx))
    edit_id = json.loads(staged)["pending_id"]
    try:
        assert file_edit_tool.get_pending(edit_id, owner="bob") is None
        assert file_edit_tool.get_pending(edit_id, owner="alice") is not None
    finally:
        file_edit_tool.remove_pending(edit_id, owner="alice")


@pytest.mark.parametrize("role, disabled, expected", [
    ("admin", False, True),
    ("user", False, False),
    ("admin", True, False),
])
def test_tool_context_only_allows_host_tools_for_active_admins(monkeypatch, role, disabled, expected):
    account = SimpleNamespace(id="u1", role=role, disabled=disabled)
    monkeypatch.setattr(routes.auth_routes, "users",
                        SimpleNamespace(find_by_id=lambda user_id: account if user_id == "u1" else None))
    with user_data.run_as("u1"):
        assert routes._tool_context().may_use_host_tools is expected
    with user_data.run_as("someone-else"):
        assert routes._tool_context().may_use_host_tools is False
    assert routes._tool_context().may_use_host_tools is False


class FakeMcpManager:
    def __init__(self):
        self.calls = []

    def get_all_openai_schemas(self, enabled_ids):
        return [{"type": "function", "function": {"name": f"mcp__{server_id}__write_file", "parameters": {}}}
                for server_id in sorted(enabled_ids)]

    async def call_tool(self, qualified_name, arguments):
        self.calls.append(qualified_name)
        return "written on the host"


def run_one_tool_call(monkeypatch, may_use_host_tools, enabled_server_ids, tool_name):
    import agent_loop

    manager = FakeMcpManager()
    offered_tools = []
    replies = iter([("tool_calls", [{"id": "c1", "name": tool_name, "arguments": {}}]), ("delta", "finished")])

    async def fake_stream(base_url, api_key, model, messages, system, options, tools=None, **kwargs):
        offered_tools.append([tool["function"]["name"] for tool in tools or []])
        yield next(replies)

    monkeypatch.setattr(agent_loop, "mcp_manager", manager)
    monkeypatch.setattr(agent_loop, "stream_chat_once", fake_stream)

    async def collect():
        return [chunk async for chunk in agent_loop.run_chat_with_tools(
            "http://model", None, "m", [{"role": "user", "content": "write it"}], None, None,
            enabled_server_ids, set(), context(may_use_host_tools))]

    events = [json.loads(chunk.decode()[len("data:"):]) for chunk in asyncio.run(collect())]
    results = [event["tool_result"]["result"] for event in events if "tool_result" in event]
    return manager.calls, offered_tools[0], results


def test_accounts_that_are_not_admins_cannot_use_mcp_servers(monkeypatch):
    calls, offered, results = run_one_tool_call(monkeypatch, False, {"fs"}, "mcp__fs__write_file")
    assert calls == []
    assert offered == []
    assert results[0].startswith("error: only an admin account can use MCP server tools")


def test_admins_can_use_the_mcp_servers_they_enabled(monkeypatch):
    calls, offered, results = run_one_tool_call(monkeypatch, True, {"fs"}, "mcp__fs__write_file")
    assert offered == ["mcp__fs__write_file"]
    assert calls == ["mcp__fs__write_file"]
    assert results == ["written on the host"]


def test_mcp_servers_that_were_not_enabled_for_the_chat_are_refused(monkeypatch):
    calls, _, results = run_one_tool_call(monkeypatch, True, {"fs"}, "mcp__github__write_file")
    assert calls == []
    assert results[0].startswith("error: the MCP server github is not enabled for this chat")
