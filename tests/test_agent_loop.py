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
import chat_stream
import reply_runs

USER_TURN = [{"role": "user", "content": "go"}]


class ScriptedModel:

    def __init__(self, *rounds):
        self.rounds = list(rounds)
        self.seen_messages = []
        self.seen_tools = []
        self.seen_think = []

    async def __call__(self, base_url, api_key, model, messages, system, options, tools=None, think=None, **kwargs):
        self.seen_messages.append([dict(m) for m in messages])
        self.seen_tools.append([tool["function"]["name"] for tool in tools or []])
        self.seen_think.append(think)
        for item in self.rounds.pop(0):
            yield item


def tool_call(name, call_id="c1", arguments=None):
    return ("tool_calls", [{"id": call_id, "name": name, "arguments": arguments or {}}])


def admin_context(events=None):
    return SimpleNamespace(may_use_host_tools=True, events=events)


def decode(chunks):
    return [json.loads(chunk.decode()[len("data:"):]) for chunk in chunks]


def run_agent(enabled_tools, ctx=None, max_tools=None):
    async def collect():
        return [chunk async for chunk in agent_loop.run_chat_with_tools(
            "http://model", None, "m", USER_TURN, None, None, set(), enabled_tools,
            ctx or admin_context(), max_tools=max_tools)]
    return decode(asyncio.run(collect()))


@pytest.fixture
def model(monkeypatch):
    def install(*rounds):
        scripted = ScriptedModel(*rounds)
        monkeypatch.setattr(agent_loop, "stream_chat_once", scripted)
        return scripted
    return install


@pytest.fixture
def tools(monkeypatch):
    calls = []

    def install(behaviour):
        async def fake_tool(name, arguments, ctx):
            calls.append((name, arguments))
            return await behaviour(name, arguments, ctx)
        monkeypatch.setattr(builtin_tools, "call_builtin_tool", fake_tool)
        return calls
    return install


def test_a_tool_that_fails_tells_the_model_why_and_the_reply_carries_on(model, tools):
    async def broken(name, arguments, ctx):
        raise OSError("disk is full")

    tools(broken)
    scripted = model([tool_call("search_web", arguments={"query": "boats"})], [("delta", "Sorry, the search failed.")])
    events = run_agent({"search_web"})

    assert {"tool_result": {"name": "search_web", "result": "error: disk is full"}} in events
    tool_message = scripted.seen_messages[1][-1]
    assert tool_message == {"role": "tool", "tool_call_id": "c1", "content": "error: disk is full"}
    assert events[-2:] == [{"delta": "Sorry, the search failed."}, {"done": True}]


def test_a_model_error_ends_the_reply_with_that_error_and_never_says_done(model, tools):
    calls = tools(lambda *args: None)
    model([("delta", "Half an ans"), ("error", "500: the model server is overloaded")])
    events = run_agent({"search_web"})

    assert events == [{"delta": "Half an ans"}, {"error": "500: the model server is overloaded"}]
    assert calls == []


def test_a_model_error_after_a_tool_round_stops_without_running_more_tools(model, tools):
    async def ok(name, arguments, ctx):
        return "found it"

    calls = tools(ok)
    model([tool_call("search_web")], [("error", "connection dropped")])
    events = run_agent({"search_web"})

    assert len(calls) == 1
    assert events[-1] == {"done": True}
    assert not any("error" in event for event in events)


def test_a_model_error_after_a_tool_result_ends_the_turn_with_a_saved_visible_note(model, tools):
    async def ok(name, arguments, ctx):
        return "found it"

    tools(ok)
    model([tool_call("search_web")], [("delta", "The page s"), ("error", "Ollama 400: m does not support images")])
    events = run_agent({"search_web"})

    assert events[-3] == {"delta": agent_loop.FAILED_AFTER_TOOLS_NOTE}
    assert events[-2] == {"failed_after_tools": "Ollama 400: m does not support images"}
    assert events[-1] == {"done": True}
    saved = reply_runs.finished_reply([chat_stream._sse_line(event) for event in events], "m")
    assert saved["content"] == "The page s" + agent_loop.FAILED_AFTER_TOOLS_NOTE
    assert "does not support images" not in saved["content"] and "400" not in saved["content"]


def test_a_collected_answer_still_raises_a_model_error_that_came_after_a_tool(model, tools):
    async def ok(name, arguments, ctx):
        return "found it"

    tools(ok)
    model([tool_call("search_web")], [("error", "the model went away")])
    with pytest.raises(RuntimeError, match="the model went away"):
        asyncio.run(agent_loop.run_chat_collected(
            "http://model", None, "m", USER_TURN, enabled_builtin_tools={"search_web"}, ctx=admin_context()))


@pytest.mark.parametrize("tools_first", [True, False])
def test_a_collected_answer_raises_a_model_error_that_has_no_text(model, tools, tools_first):
    async def ok(name, arguments, ctx):
        return "found it"

    tools(ok)
    script = [[tool_call("search_web")], [("delta", "Half an ans"), ("error", "")]]
    model(*(script if tools_first else script[1:]))
    with pytest.raises(RuntimeError):
        asyncio.run(agent_loop.run_chat_collected(
            "http://model", None, "m", USER_TURN, enabled_builtin_tools={"search_web"}, ctx=admin_context()))


def test_a_reply_that_is_stopped_before_its_tool_runs_neither_runs_it_nor_asks_the_model_again(model, tools):
    async def should_not_run(name, arguments, ctx):
        raise AssertionError("the tool ran after the reply was stopped")

    calls = tools(should_not_run)
    scripted = model([tool_call("run_shell", arguments={"command": "rm -rf build"})], [("delta", "never asked")])

    async def stop_at_the_tool():
        stream = agent_loop.run_chat_with_tools("http://model", None, "m", USER_TURN, None, None, set(),
                                                {"run_shell"}, admin_context())
        seen = []
        async for chunk in stream:
            seen.extend(decode([chunk]))
            if "tool_start" in seen[-1]:
                await stream.aclose()
                break
        return seen

    seen = asyncio.run(stop_at_the_tool())
    assert seen == [{"tool_start": "run_shell"}]
    assert calls == []
    assert len(scripted.seen_messages) == 1


def test_cancelling_a_reply_while_its_tool_runs_cancels_the_tool_and_asks_the_model_nothing_more(model, tools):
    tool_was_cancelled = []

    async def slow(name, arguments, ctx):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            tool_was_cancelled.append(True)
            raise
        return "finished"

    tools(slow)
    scripted = model([tool_call("deep_research")], [("delta", "never asked")])

    async def cancel_mid_tool():
        async def consume():
            return [chunk async for chunk in agent_loop.run_chat_with_tools(
                "http://model", None, "m", USER_TURN, None, None, set(), {"deep_research"}, admin_context())]
        task = asyncio.ensure_future(consume())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

    asyncio.run(cancel_mid_tool())
    assert tool_was_cancelled == [True]
    assert len(scripted.seen_messages) == 1


def test_progress_a_tool_reports_reaches_the_page_before_its_result(model, tools):
    async def reports_progress(name, arguments, ctx):
        await ctx.events.put({"progress": "reading page 1"})
        await asyncio.sleep(0.01)
        await ctx.events.put({"progress": "reading page 2"})
        return "summary"

    tools(reports_progress)
    model([tool_call("deep_research")], [("delta", "Here it is.")])

    async def collect():
        ctx = admin_context(events=asyncio.Queue())
        return decode([chunk async for chunk in agent_loop.run_chat_with_tools(
            "http://model", None, "m", USER_TURN, None, None, set(), {"deep_research"}, ctx)])

    events = asyncio.run(collect())
    assert events[:4] == [
        {"tool_start": "deep_research"},
        {"progress": "reading page 1"},
        {"progress": "reading page 2"},
        {"tool_result": {"name": "deep_research", "result": "summary"}},
    ]


def test_a_small_model_is_offered_only_as_many_tools_as_it_can_handle(model):
    scripted = model([("delta", "hello")])
    run_agent({"search_web", "manage_memory", "manage_notes"}, max_tools=2)
    assert len(scripted.seen_tools[0]) == 2


def test_a_collected_answer_joins_the_streamed_words_and_passes_thinking_through(model):
    scripted = model([("delta", "Hello "), ("delta", "there.")])
    answer = asyncio.run(agent_loop.run_chat_collected("http://model", None, "m", USER_TURN, think=False))
    assert answer == "Hello there."
    assert scripted.seen_think == [False]
    assert scripted.seen_tools == [[]]


def test_a_collected_answer_raises_the_model_error_instead_of_returning_half_an_answer(model):
    model([("delta", "Half"), ("error", "401: bad api key")])
    with pytest.raises(RuntimeError, match="401: bad api key"):
        asyncio.run(agent_loop.run_chat_collected("http://model", None, "m", USER_TURN))


def test_a_collected_answer_with_tools_keeps_only_the_answer_text(model, tools):
    async def ok(name, arguments, ctx):
        return "a tool result that is not part of the answer"

    tools(ok)
    model([("delta", "Let me look. "), tool_call("search_web")], [("delta", "It is sunny.")])
    answer = asyncio.run(agent_loop.run_chat_collected(
        "http://model", None, "m", USER_TURN, enabled_builtin_tools={"search_web"}, ctx=admin_context()))
    assert answer == "Let me look. It is sunny."


def test_a_collected_answer_with_tools_raises_a_model_error(model, tools):
    tools(lambda *args: None)
    model([("error", "the model went away")])
    with pytest.raises(RuntimeError, match="the model went away"):
        asyncio.run(agent_loop.run_chat_collected(
            "http://model", None, "m", USER_TURN, enabled_builtin_tools={"search_web"}, ctx=admin_context()))


@pytest.mark.parametrize("chunk", [b"", b"event: ping\n\n", b"data: {not json}\n\n", b'data: {"tool_start": "x"}\n\n'])
def test_stream_lines_that_are_not_answer_text_or_errors_are_left_out_of_a_collected_answer(chunk):
    assert list(agent_loop._parse_sse_bytes(chunk)) == []


def test_a_tool_the_chat_did_not_enable_is_refused_and_not_run(monkeypatch):
    ran = []
    replies = iter([("tool_calls", [{"id": "c1", "name": "manage_notes", "arguments": {"action": "add", "title": "x"}}]),
                    ("delta", "finished")])

    async def fake_stream(base_url, api_key, model, messages, system, options, tools=None, **kwargs):
        yield next(replies)

    async def tool(name, arguments, ctx):
        ran.append(name)
        return "done"

    monkeypatch.setattr(agent_loop, "stream_chat_once", fake_stream)
    monkeypatch.setattr(agent_loop.builtin_tools, "call_builtin_tool", tool)

    async def collect():
        return [chunk async for chunk in agent_loop.run_chat_with_tools(
            "http://model", None, "m", [{"role": "user", "content": "hi"}], None, None, set(), {"search_web"},
            SimpleNamespace(may_use_host_tools=False, untrusted_read=False, events=None))]

    events = [json.loads(chunk.decode()[len("data:"):]) for chunk in asyncio.run(collect())]
    results = [event["tool_result"]["result"] for event in events if "tool_result" in event]
    assert ran == [] and results == ["error: the tool manage_notes is not enabled for this chat, so it was not run"]


@pytest.mark.parametrize("arguments", [{"action": ["send"]}, {"action": {"x": 1}}, ["send"]])
def test_odd_argument_types_from_the_model_get_an_error_result_not_a_crash(arguments):
    assert not agent_loop.builtin_tools.reads_untrusted_content("manage_email", arguments)
    assert not agent_loop.builtin_tools.changes_state("manage_email", arguments)
    result = asyncio.run(agent_loop.builtin_tools.call_builtin_tool("manage_email", arguments, None))
    assert result.startswith("error:")


@pytest.mark.parametrize("name", [None, 5])
def test_a_tool_call_whose_name_is_not_text_is_refused_as_not_enabled(name):
    assert not builtin_tools.is_mcp_tool(name)
    refusal = agent_loop._refuse_tool_not_offered(name, frozenset({"search_web"}))
    assert "is not enabled for this chat" in refusal
