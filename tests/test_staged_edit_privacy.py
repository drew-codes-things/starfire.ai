import asyncio
import json
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import pytest

import agent_loop
import builtin_tools
import file_edit_tool
import user_data
from host_sandbox import HostLimits

OLD_TEXT = "db_password = existing-value-only-the-user-should-see\n"


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "settings.ini").write_text(OLD_TEXT)
    yield tmp_path
    for edit in list(file_edit_tool._pending.values()):
        file_edit_tool.remove_pending(edit["id"], edit["owner"])


def staged_edit(workspace, owner="alice"):
    ctx = SimpleNamespace(may_use_host_tools=True, require_edit_approval=True,
                          host_limits=HostLimits(workspace=str(workspace)))
    with user_data.run_as(owner):
        return asyncio.run(builtin_tools.call_builtin_tool(
            "edit_file", {"path": "settings.ini", "content": "db_password = new\n"}, ctx))


def test_the_model_learns_only_that_the_edit_waits_for_approval(workspace):
    told = json.loads(staged_edit(workspace))
    assert "existing-value" not in json.dumps(told) and "diff" not in told
    assert told["staged"] is True and told["status"] == builtin_tools.EDIT_AWAITS_APPROVAL
    assert (workspace / "settings.ini").read_text() == OLD_TEXT


def test_the_page_still_gets_the_diff_for_its_approval_card(workspace):
    told = staged_edit(workspace)
    with user_data.run_as("alice"):
        shown = json.loads(agent_loop._result_for_page("edit_file", told))
    assert "-db_password = existing-value-only-the-user-should-see" in shown["diff"]
    assert "+db_password = new" in shown["diff"]


def test_another_account_never_gets_someone_elses_diff(workspace):
    told = staged_edit(workspace)
    with user_data.run_as("bob"):
        shown = json.loads(agent_loop._result_for_page("edit_file", told))
    assert "diff" not in shown
