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
