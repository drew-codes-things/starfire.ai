import asyncio
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import agent_loop
import builtin_tools
import shell_tool


def context(require_approval: bool, events=None):
    ctx = SimpleNamespace(require_shell_approval=require_approval, events=events)

    async def emit(event):
        if events is not None:
            await events.put(event)

    ctx.emit = emit
    return ctx


def test_approved_command_runs_and_rejected_command_does_not(monkeypatch):
    async def run():
        monkeypatch.setattr(shell_tool, "APPROVAL_TIMEOUT_SECONDS", 5)
        events = asyncio.Queue()
        ctx = context(True, events)

        async def decide(approved):
            event = await events.get()
            assert event["shell_pending"]["command"] == "echo approved"
            assert shell_tool.resolve(event["shell_pending"]["pending_id"], approved, owner=None)

        decision = asyncio.ensure_future(decide(True))
        result = await builtin_tools._run_shell({"command": "echo approved"}, ctx)
        await decision
        assert "approved" in result and result.startswith("(exit code 0)")

        decision = asyncio.ensure_future(decide(False))
        result = await builtin_tools._run_shell({"command": "echo approved"}, ctx)
        await decision
        assert result.startswith("error: the user rejected")
        assert shell_tool.list_pending(None) == []

    asyncio.run(run())


def test_unanswered_command_times_out(monkeypatch):
    async def run():
        monkeypatch.setattr(shell_tool, "APPROVAL_TIMEOUT_SECONDS", 0.05)
        result = await builtin_tools._run_shell({"command": "echo never"}, context(True, asyncio.Queue()))
        assert result.startswith("error: the user did not approve")
        assert shell_tool.list_pending(None) == []

    asyncio.run(run())


def test_without_approval_the_command_just_runs():
    result = asyncio.run(builtin_tools._run_shell({"command": "echo plain"}, context(False)))
    assert "plain" in result


def test_execute_tool_streams_events_while_the_tool_waits():
    async def run():
        events = asyncio.Queue()
        ctx = SimpleNamespace(events=events)

        async def slow_tool(name, arguments, ctx):
            await ctx.events.put({"shell_pending": {"pending_id": "p1", "command": "ls"}})
            await asyncio.sleep(0.01)
            return "done"

        original = builtin_tools.call_builtin_tool
        builtin_tools.call_builtin_tool = slow_tool
        try:
            seen = []
            async for kind, payload in agent_loop._execute_tool({"name": "run_shell", "arguments": {}}, ctx):
                seen.append((kind, payload))
        finally:
            builtin_tools.call_builtin_tool = original
        assert seen == [("event", {"shell_pending": {"pending_id": "p1", "command": "ls"}}), ("result", "done")]

    asyncio.run(run())


def stream_one_tool_result(monkeypatch, tool_name, tool_result):
    import json

    replies = iter([("tool_calls", [{"id": "c1", "name": tool_name, "arguments": {}}]), ("delta", "finished")])

    async def fake_stream(base_url, api_key, model, messages, system, options, tools=None, **kwargs):
        yield next(replies)

    async def fake_tool(name, arguments, ctx):
        return tool_result

    monkeypatch.setattr(agent_loop, "stream_chat_once", fake_stream)
    monkeypatch.setattr(builtin_tools, "call_builtin_tool", fake_tool)

    async def collect():
        return [chunk async for chunk in agent_loop.run_chat_with_tools(
            "http://model", None, "m", [{"role": "user", "content": "go"}], None, None,
            set(), {tool_name}, SimpleNamespace(may_use_host_tools=True, events=None))]

    events = [json.loads(chunk.decode()[len("data:"):]) for chunk in asyncio.run(collect())]
    return next(event["tool_result"]["result"] for event in events if "tool_result" in event)


def test_a_long_edit_result_reaches_the_page_whole_so_its_diff_and_approval_show(monkeypatch):
    import json

    long_diff = "\n".join(f"+line {number}" for number in range(1000))
    staged = json.dumps({"staged": True, "pending_id": "p1", "path": "/srv/x.py", "diff": long_diff})
    sent = stream_one_tool_result(monkeypatch, "edit_file", staged)
    assert json.loads(sent) == {"staged": True, "pending_id": "p1", "path": "/srv/x.py", "diff": long_diff}


def test_long_plain_tool_output_is_still_shortened_for_the_page(monkeypatch):
    sent = stream_one_tool_result(monkeypatch, "run_shell", "x" * 50000)
    assert sent == "x" * agent_loop.TOOL_RESULT_DISPLAY_CHARS


def test_a_model_that_keeps_calling_tools_is_stopped_with_a_clear_answer(monkeypatch):
    import json

    executed = []

    async def always_a_tool_call(base_url, api_key, model, messages, system, options, tools=None, **kwargs):
        yield ("tool_calls", [{"id": f"c{len(executed)}", "name": "run_shell", "arguments": {"command": "ls"}}])

    async def fake_tool(name, arguments, ctx):
        executed.append(name)
        return "listed"

    monkeypatch.setattr(agent_loop, "stream_chat_once", always_a_tool_call)
    monkeypatch.setattr(builtin_tools, "call_builtin_tool", fake_tool)

    async def collect():
        return [chunk async for chunk in agent_loop.run_chat_with_tools(
            "http://model", None, "m", [{"role": "user", "content": "go"}], None, None,
            set(), {"run_shell"}, SimpleNamespace(may_use_host_tools=True, events=None))]

    events = [json.loads(chunk.decode()[len("data:"):]) for chunk in asyncio.run(collect())]
    assert len(executed) == agent_loop.MAX_TOOL_ROUNDS
    assert not any("error" in event for event in events)
    assert events[-1] == {"done": True}
    note = events[-2]["delta"]
    assert f"Stopped after {agent_loop.MAX_TOOL_ROUNDS} rounds of tool calls" in note
    assert "run_shell" in note


def test_a_command_reports_its_exit_code_and_runs_in_the_folder_asked_for(tmp_path):
    result = asyncio.run(shell_tool.run_shell("pwd; exit 3", str(tmp_path)))
    assert result.splitlines() == ["(exit code 3)", str(tmp_path)]


def test_a_command_in_a_missing_folder_is_an_error_not_a_crash(tmp_path):
    result = asyncio.run(shell_tool.run_shell("echo hi", str(tmp_path / "missing")))
    assert result.startswith("error: failed to start command")


def test_a_blank_command_is_refused():
    assert asyncio.run(shell_tool.run_shell("   ")) == "error: command is required"
    assert asyncio.run(builtin_tools._run_shell({"command": ""}, context(True))) == "error: command is required"


def test_a_command_that_runs_too_long_is_stopped(monkeypatch):
    monkeypatch.setattr(shell_tool, "TIMEOUT_SECONDS", 0.2)
    result = asyncio.run(shell_tool.run_shell("sleep 5; echo finished"))
    assert result == "error: command timed out after 0.2s"


def test_long_output_is_cut_and_says_so(monkeypatch):
    monkeypatch.setattr(shell_tool, "MAX_OUTPUT_CHARS", 10)
    result = asyncio.run(shell_tool.run_shell("printf 'abcdefghijklmnopqrstuvwxyz'"))
    assert result == "(exit code 0)\nabcdefghij\n... (truncated, output exceeded 10 characters)"


def test_waiting_on_a_command_that_was_never_staged_gives_no_decision():
    assert asyncio.run(shell_tool.wait_for_decision("never-staged")) is None
