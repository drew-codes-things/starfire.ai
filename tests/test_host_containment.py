import asyncio
import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import builtin_tools
import file_edit_tool
import github_tool
import host_sandbox
import shell_tool
from api_key_manager import APIKeyManager
from builtin_tools import ToolContext
from host_sandbox import HostLimits

FAKE_SECRET = "fake-secret-value-for-env-scrub"
HAS_SANDBOX = host_sandbox.working_sandbox_program() is not None


@pytest.fixture
def layout(tmp_path):
    data = tmp_path / "data"
    app = tmp_path / "app"
    workspace = data / "users" / ("a" * 32) / "workspace"
    for folder in (data, app, workspace):
        folder.mkdir(parents=True, exist_ok=True)
    (data / "users.json").write_text('[{"password_hash": "$2b$12$secret"}]')
    (app / ".env").write_text(f"ARTY_ACCESS_CLIENT_SECRET={FAKE_SECRET}\n")
    limits = HostLimits(workspace=str(workspace), hidden_dirs=(str(data), str(app)), protected=(str(data), str(app)))
    return {"data": data, "app": app, "workspace": workspace, "limits": limits, "outside": tmp_path / "outside"}


@pytest.fixture
def secret_env(monkeypatch):
    monkeypatch.setenv("ARTY_ACCESS_CLIENT_SECRET", FAKE_SECRET)
    monkeypatch.setenv("GH_TOKEN", "gh-placeholder-token")


@pytest.fixture
def no_sandbox(monkeypatch):
    monkeypatch.setattr(host_sandbox, "sandbox_program", lambda: None)


def shell(command, limits, cwd=None):
    return asyncio.run(shell_tool.run_shell(command, cwd, limits))


def test_a_shell_command_never_sees_starfires_secrets_in_its_environment(layout, secret_env, no_sandbox):
    result = shell("env", layout["limits"])
    assert result.startswith("(exit code 0)")
    assert FAKE_SECRET not in result and "gh-placeholder-token" not in result and "PATH=" in result


def test_a_shell_command_starts_in_the_accounts_workspace(layout, no_sandbox):
    assert shell("pwd", layout["limits"]).splitlines()[1] == str(layout["workspace"])


def test_a_command_that_runs_too_long_is_stopped_with_everything_it_started(layout, no_sandbox, monkeypatch):
    monkeypatch.setattr(shell_tool, "TIMEOUT_SECONDS", 0.5)
    marker = layout["outside"]
    started = time.monotonic()
    result = shell(f"(sleep 1.5; touch {marker}) & sleep 30", layout["limits"])
    assert result == "error: command timed out after 0.5s" and time.monotonic() - started < 5
    time.sleep(2)
    assert not marker.exists()


def test_endless_output_is_cut_off_without_reading_all_of_it(layout, no_sandbox):
    started = time.monotonic()
    result = shell("yes starfire", layout["limits"])
    assert time.monotonic() - started < 10
    assert "truncated" in result and len(result) < shell_tool.MAX_OUTPUT_CHARS + 200


@pytest.mark.skipif(not HAS_SANDBOX, reason="bubblewrap is not installed here")
def test_in_the_sandbox_the_data_and_app_folders_are_hidden_and_only_the_workspace_is_writable(layout, secret_env):
    limits = layout["limits"]
    result = shell(f"cat {layout['data']}/users.json; cat {layout['app']}/.env; env; echo kept > made.txt; "
                   f"touch {layout['outside']}; echo done", limits)
    assert "password_hash" not in result and FAKE_SECRET not in result
    assert (layout["workspace"] / "made.txt").read_text() == "kept\n"
    assert not layout["outside"].exists()
    assert result.rstrip().endswith("done")


@pytest.mark.skipif(not HAS_SANDBOX, reason="bubblewrap is not installed here")
def test_in_the_sandbox_a_command_cannot_see_other_processes(layout):
    result = shell(f"ls /proc | grep -c '^[0-9]'; test -d /proc/{os.getpid()} && echo seen || echo hidden",
                   layout["limits"])
    count, starfire = result.splitlines()[1:3]
    assert int(count) <= 5 and starfire == "hidden"


def test_without_a_sandbox_the_shell_still_runs_and_says_why_in_the_log(layout, no_sandbox, caplog):
    assert "hello" in shell("echo hello", layout["limits"])
    assert "without a sandbox" in caplog.text


def test_a_bubblewrap_that_cannot_start_runs_the_shell_as_without_a_sandbox(layout, tmp_path, monkeypatch, caplog):
    broken = tmp_path / "bwrap"
    broken.write_text("#!/bin/sh\necho 'bwrap: setting up uid map: Permission denied' >&2\nexit 1\n")
    broken.chmod(0o755)
    monkeypatch.setattr(host_sandbox, "sandbox_program", lambda: str(broken))
    assert "hello" in shell("echo hello", layout["limits"])
    assert "without a sandbox" in caplog.text and "cannot start" in caplog.text


def edit(ctx, path, content="new\n"):
    return asyncio.run(builtin_tools.call_builtin_tool("edit_file", {"path": path, "content": content}, ctx))


@pytest.fixture
def ctx(layout, tmp_path):
    return ToolContext(memory=None, documents=None, tasks=None, task_runs=None, email_accounts=None,
                       api_keys=APIKeyManager(str(tmp_path)), notes=None, may_use_host_tools=True,
                       require_edit_approval=False, host_limits=layout["limits"])


def test_a_relative_edit_lands_in_the_workspace(ctx, layout):
    result = json.loads(edit(ctx, "notes/today.txt"))
    assert result["applied"] is True
    assert (layout["workspace"] / "notes" / "today.txt").read_text() == "new\n"


@pytest.mark.parametrize("target", ["outside", "users.json", "env", "escape", "other_workspace"])
def test_an_edit_outside_the_workspace_is_refused_without_reading_the_file(ctx, layout, target):
    paths = {
        "outside": str(layout["outside"]),
        "users.json": str(layout["data"] / "users.json"),
        "env": str(layout["app"] / ".env"),
        "escape": "../../../users.json",
        "other_workspace": str(layout["data"] / "users" / ("b" * 32) / "workspace" / "x.txt"),
    }
    result = edit(ctx, paths[target], content="")
    assert result.startswith("error: edit_file can only change files")
    assert "password_hash" not in result and FAKE_SECRET not in result
    assert (layout["data"] / "users.json").read_text().startswith("[{") and not layout["outside"].exists()


def test_a_link_in_the_workspace_that_points_outside_is_refused(ctx, layout):
    (layout["workspace"] / "sneaky").symlink_to(layout["data"] / "users.json")
    assert edit(ctx, "sneaky", content="").startswith("error: edit_file can only change files")
    assert "password_hash" in (layout["data"] / "users.json").read_text()


def test_a_folder_an_admin_allowed_can_be_edited_but_starfires_own_folders_never(layout, ctx, tmp_path):
    allowed = tmp_path / "projects"
    allowed.mkdir()
    ctx.host_limits = HostLimits(workspace=layout["limits"].workspace, protected=layout["limits"].protected,
                                 edit_roots=(str(allowed), str(tmp_path)))
    assert json.loads(edit(ctx, str(allowed / "readme.md")))["applied"] is True
    assert edit(ctx, str(layout["data"] / "users.json"), "").startswith("error: edit_file can only change files")


def test_a_staged_edit_is_checked_again_when_it_is_approved(layout):
    target = layout["workspace"] / "plan.txt"
    staged = file_edit_tool.resolve_target("plan.txt", layout["limits"])
    target.symlink_to(layout["data"] / "users.json")
    result = file_edit_tool.apply_edit(staged, "", layout["limits"])
    assert result["ok"] is False and "password_hash" in (layout["data"] / "users.json").read_text()


def test_github_cli_gets_only_its_own_token_from_the_environment(tmp_path, secret_env, monkeypatch):
    fake_gh = tmp_path / "bin" / "gh"
    fake_gh.parent.mkdir()
    fake_gh.write_text("#!/bin/sh\nenv\n")
    fake_gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_gh.parent}:{os.environ['PATH']}")
    result = asyncio.run(github_tool.run_gh(["auth", "status"]))
    assert "GH_TOKEN=gh-placeholder-token" in result and FAKE_SECRET not in result


def test_a_failed_sandbox_probe_is_tried_again_after_a_while(tmp_path, monkeypatch):
    program = tmp_path / "bwrap"
    program.write_text("#!/bin/sh\nexit 1\n")
    program.chmod(0o755)
    monkeypatch.setattr(host_sandbox, "SANDBOX_RETRY_SECONDS", 3600, raising=False)
    assert host_sandbox.sandbox_starts(str(program)) is False
    program.write_text("#!/bin/sh\nexit 0\n")
    assert host_sandbox.sandbox_starts(str(program)) is False
    monkeypatch.setattr(host_sandbox, "SANDBOX_RETRY_SECONDS", 0, raising=False)
    assert host_sandbox.sandbox_starts(str(program)) is True


def test_a_working_sandbox_is_probed_once(tmp_path):
    program = tmp_path / "bwrap"
    count = tmp_path / "count"
    program.write_text(f"#!/bin/sh\necho x >> {count}\nexit 0\n")
    program.chmod(0o755)
    assert all(host_sandbox.sandbox_starts(str(program)) for _ in range(3))
    assert count.read_text().count("x") == 1
