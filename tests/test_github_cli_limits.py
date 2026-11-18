import asyncio
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import builtin_tools
import github_tool
import shell_tool
from api_key_manager import APIKeyManager
from builtin_tools import ToolContext


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    marker = tmp_path / "gh-ran.txt"
    program = tmp_path / "bin" / "gh"
    program.parent.mkdir()
    program.write_text(f'#!/bin/sh\necho "$@" >> {marker}\necho gh-output\n')
    program.chmod(0o755)
    monkeypatch.setenv("PATH", f"{program.parent}:{os.environ['PATH']}")
    return marker


def context(tmp_path, require_shell_approval=True):
    return ToolContext(memory=None, documents=None, tasks=None, task_runs=None, email_accounts=None,
                       api_keys=APIKeyManager(str(tmp_path)), notes=None, may_use_host_tools=True,
                       require_shell_approval=require_shell_approval, events=asyncio.Queue())


def gh(args, ctx):
    return asyncio.run(builtin_tools.call_builtin_tool("github_cli", {"args": args}, ctx))


@pytest.mark.parametrize("args", [
    ["alias", "set", "--shell", "audit", "id > /tmp/x"],
    ["alias", "import", "aliases.yml"],
    ["extension", "install", "someone/gh-anything"],
    ["ext", "install", "someone/gh-anything"],
    ["config", "set", "editor", "sh -c id"],
    ["auth", "login", "--with-token"],
    ["auth", "token"],
    ["secret", "set", "X"],
    ["audit"],
    ["--version"],
])
def test_commands_that_can_run_programs_or_change_sign_in_are_never_run(tmp_path, fake_gh, args):
    for approval in (True, False):
        result = gh(args, context(tmp_path, require_shell_approval=approval))
        assert result.startswith("error: github_cli does not run")
    assert not fake_gh.exists() and shell_tool.list_pending(None) == []


@pytest.mark.parametrize("args", [["issue", "list"], ["pr", "view", "12"], ["search", "repos", "starfire"],
                                  ["auth", "status"], ["run", "view", "5", "--log"]])
def test_read_only_commands_run_without_a_card(tmp_path, fake_gh, args):
    assert "gh-output" in gh(args, context(tmp_path))
    assert fake_gh.read_text().split() == args


@pytest.mark.parametrize("args", [["issue", "create", "--title", "x"], ["repo", "delete", "mine", "--yes"],
                                  ["api", "-X", "DELETE", "repos/me/x"], ["pr", "merge", "3"]])
def test_commands_that_change_things_wait_for_approval(tmp_path, fake_gh, monkeypatch, args):
    monkeypatch.setattr(shell_tool, "APPROVAL_TIMEOUT_SECONDS", 0.2)
    ctx = context(tmp_path)

    async def run():
        result = await builtin_tools.call_builtin_tool("github_cli", {"args": args}, ctx)
        return result, ctx.events.get_nowait()

    result, event = asyncio.run(run())
    assert result.startswith("error: the user did not approve this gh command")
    assert event["shell_pending"]["tool"] == "github_cli" and event["shell_pending"]["command"].startswith("gh ")
    assert not fake_gh.exists()


def test_with_shell_approval_off_a_changing_command_runs_as_the_admin_chose(tmp_path, fake_gh):
    assert "gh-output" in gh(["issue", "create", "--title", "x"], context(tmp_path, require_shell_approval=False))


def test_args_that_are_not_a_list_of_strings_are_refused(tmp_path, fake_gh):
    assert gh("issue list", context(tmp_path)) == "error: args must be a list of strings"
    assert gh(["issue", 3], context(tmp_path)) == "error: args must be a list of strings"
    assert asyncio.run(github_tool.run_gh([])) == github_tool.ARGS_REQUIRED


@pytest.mark.parametrize("args", [
    ["auth", "status", "--show-token"],
    ["auth", "status", "-t"],
    ["auth", "status", "--show-token=true"],
    ["auth", "token"],
    ["issue", "list", "--json", "number", "--jq", "env.GH_TOKEN"],
    ["issue", "list", "--json", "number", "--jq=env.GH_TOKEN"],
    ["issue", "list", "--json", "number", "-q", "env.GH_TOKEN"],
    ["issue", "view", "1", "-cq", "env.GH_TOKEN"],
    ["pr", "list", "--json", "title", "--template", "{{.}}"],
    ["pr", "list", "--json", "title", "-t", "{{.}}"],
    ["api", "user", "--jq", "$ENV.GH_TOKEN"],
    ["project", "list", "-q", "env.GH_TOKEN"],
    ["cache", "list", "-q", "env.GH_TOKEN"],
    ["org", "list", "-q", "env.GH_TOKEN"],
    ["ruleset", "list", "-q", "env.GH_TOKEN"],
    ["auth", "status", "--json", "token"],
    ["issue", "list", "--show-token"],
])
def test_flags_that_can_print_the_token_are_never_run(tmp_path, fake_gh, monkeypatch, args):
    monkeypatch.setattr(shell_tool, "APPROVAL_TIMEOUT_SECONDS", 0.2)
    for approval in (True, False):
        result = gh(args, context(tmp_path, require_shell_approval=approval))
        assert result.startswith("error: github_cli does not"), result
    assert not fake_gh.exists() and shell_tool.list_pending(None) == []


@pytest.mark.parametrize("args", [["issue", "list", "--web"], ["repo", "view", "--branch", "x", "--unknown-flag"],
                                  ["pr", "view", "3", "-w"]])
def test_a_read_command_with_a_flag_outside_the_allow_list_waits_for_approval(tmp_path, fake_gh, monkeypatch, args):
    monkeypatch.setattr(shell_tool, "APPROVAL_TIMEOUT_SECONDS", 0.2)
    result = gh(args, context(tmp_path))
    assert result.startswith("error: the user did not approve this gh command") and not fake_gh.exists()


def test_reading_github_content_counts_as_untrusted_content():
    assert builtin_tools.reads_untrusted_content("github_cli", {"args": ["issue", "view", "1", "--comments"]})
