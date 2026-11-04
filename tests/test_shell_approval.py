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
