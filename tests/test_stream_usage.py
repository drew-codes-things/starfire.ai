import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import httpx
import pytest

import agent_loop
import builtin_tools
import chat_stream
from config import config

HELLO = [{"role": "user", "content": "hello"}]
OLLAMA_URL = "http://localhost:11434"
OPENAI_URL = "https://models.example.com/v1"
ANTHROPIC_URL = "https://api.anthropic.com"


def sse(*chunks) -> str:
    return "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"


def ndjson(*chunks) -> str:
    return "\n".join(json.dumps(chunk) for chunk in chunks) + "\n"


@pytest.fixture
def model_server(monkeypatch):
    requests = []
    replies = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        status, body = replies.pop(0) if len(replies) > 1 else replies[0]
        return httpx.Response(status, text=body)

    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(chat_stream.httpx, "AsyncClient", client)
    monkeypatch.setattr(config, "ollama_base_url", None)
    return requests, replies


def stream_once(base_url, options=None, api_key="k"):
    async def collect():
        return [item async for item in chat_stream.stream_chat_once(base_url, api_key, "m", HELLO, None, options)]
    return asyncio.run(collect())


def stream_events(base_url, options=None):
    async def collect():
        return [chunk async for chunk in chat_stream.stream_chat(base_url, "k", "m", HELLO, None, options)]
    return [json.loads(chunk.decode()[len("data: "):]) for chunk in asyncio.run(collect())]


def usage_of(items):
    return [payload for kind, payload in items if kind == "usage"]


def test_an_arty_engine_reply_reports_its_tokens_cache_speed_and_the_window_asked_for(model_server, monkeypatch):
    _, replies = model_server
    monkeypatch.setattr(config, "task_num_ctx", 12288)
    replies.append((200, ndjson(
        {"message": {"content": "Hi"}, "done": False},
        {"message": {"content": ""}, "done": True, "prompt_eval_count": 1200, "prompt_eval_cached_count": 1188,
         "prompt_eval_duration": 40_000_000, "eval_count": 54, "eval_duration": 2_000_000_000})))
    assert usage_of(stream_once(OLLAMA_URL)) == [{
        "prompt_tokens": 1200, "completion_tokens": 54, "cached_prompt_tokens": 1188,
        "context_window": 12288, "generation_seconds": 2.0}]


def test_a_stock_ollama_reply_has_no_cache_figure_and_the_chats_own_window_wins(model_server):
    _, replies = model_server
    replies.append((200, ndjson(
        {"message": {"content": "Hi"}},
        {"done": True, "prompt_eval_count": 30, "eval_count": 12, "eval_duration": 400_000_000})))
    [usage] = usage_of(stream_once(OLLAMA_URL, options={"num_ctx": 4096}))
    assert usage == {"prompt_tokens": 30, "completion_tokens": 12, "context_window": 4096, "generation_seconds": 0.4}
    assert "cached_prompt_tokens" not in usage


def test_an_ollama_reply_without_counts_reports_no_usage(model_server):
    _, replies = model_server
    replies.append((200, ndjson({"message": {"content": "Hi"}}, {"done": True})))
    assert stream_once(OLLAMA_URL) == [("delta", "Hi")]


def test_openai_style_servers_are_asked_for_usage_and_its_cached_tokens_are_passed_on(model_server):
    requests, replies = model_server
    replies.append((200, sse(
        {"choices": [{"delta": {"content": "Hi"}, "finish_reason": None}], "usage": None},
        {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": None},
        {"choices": [], "usage": {"prompt_tokens": 2006, "completion_tokens": 300, "total_tokens": 2306,
                                  "prompt_tokens_details": {"cached_tokens": 1920}}})))
    items = stream_once(OPENAI_URL)
    assert requests[0]["stream_options"] == {"include_usage": True}
    assert items == [("delta", "Hi"), ("usage", {"prompt_tokens": 2006, "completion_tokens": 300,
                                                 "cached_prompt_tokens": 1920})]


def test_the_arty_brains_usage_on_its_last_chunk_is_passed_on_without_a_cache_figure(model_server):
    _, replies = model_server
    replies.append((200, sse(
        {"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Hi."}, "finish_reason": None}]},
        {"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 5400, "completion_tokens": 80, "total_tokens": 5480}})))
    assert usage_of(stream_once("http://brain.example:8081/v1")) == [{"prompt_tokens": 5400, "completion_tokens": 80}]


def test_llama_cpp_timings_give_the_whole_prompt_the_cache_share_and_the_generation_time(model_server):
    _, replies = model_server
    replies.append((200, sse(
        {"choices": [{"delta": {"content": "Hi"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 12, "completion_tokens": 40},
         "timings": {"prompt_n": 12, "cache_n": 150, "prompt_ms": 30.5, "predicted_n": 40, "predicted_ms": 1600.0}})))
    assert usage_of(stream_once(OPENAI_URL)) == [{"prompt_tokens": 162, "completion_tokens": 40,
                                                  "cached_prompt_tokens": 150, "generation_seconds": 1.6}]


def test_a_server_that_refuses_the_usage_option_is_asked_again_without_it(model_server):
    requests, replies = model_server
    replies.append((400, '{"error": {"message": "Unrecognized request argument supplied: stream_options"}}'))
    replies.append((200, sse({"choices": [{"delta": {"content": "Hi"}}]})))
    assert stream_once(OPENAI_URL) == [("delta", "Hi")]
    assert [("stream_options" in sent) for sent in requests] == [True, False]


def test_other_refusals_are_reported_once_and_not_retried(model_server):
    requests, replies = model_server
    replies.append((401, '{"error": {"message": "that key is not valid"}}'))
    assert stream_once(OPENAI_URL) == [("error", "401: that key is not valid")]
    assert len(requests) == 1


def test_an_anthropic_reply_counts_cache_reads_and_writes_as_part_of_the_prompt(model_server):
    _, replies = model_server
    replies.append((200, "\n".join([
        "event: message_start",
        'data: {"type": "message_start", "message": {"usage": {"input_tokens": 20, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 880, "output_tokens": 1}}}', "",
        "event: content_block_delta", 'data: {"index": 0, "delta": {"type": "text_delta", "text": "Hello"}}', "",
        "event: message_delta", 'data: {"delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 15}}', "",
        "event: message_stop", "data: {}", ""])))
    assert stream_once(ANTHROPIC_URL, api_key="sk-ant-PLACEHOLDER") == [
        ("delta", "Hello"), ("usage", {"prompt_tokens": 1000, "completion_tokens": 15, "cached_prompt_tokens": 880})]


def test_nonsense_counts_are_left_out_rather_than_passed_on(model_server):
    _, replies = model_server
    replies.append((200, ndjson({"done": True, "prompt_eval_count": "lots", "eval_count": -3, "eval_duration": True})))
    assert stream_once(OLLAMA_URL) == []


def test_the_page_stream_carries_the_usage_line_before_done(model_server):
    _, replies = model_server
    replies.append((200, sse(
        {"choices": [{"delta": {"content": "Hi"}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 10, "completion_tokens": 2}})))
    assert stream_events(OPENAI_URL) == [
        {"delta": "Hi"}, {"usage": {"prompt_tokens": 10, "completion_tokens": 2}}, {"done": True}]


class ScriptedRounds:

    def __init__(self, *rounds):
        self.rounds = list(rounds)

    async def __call__(self, *args, **kwargs):
        for item in self.rounds.pop(0):
            yield item


def test_a_reply_with_tool_steps_sends_the_usage_of_every_round(monkeypatch):
    first = {"prompt_tokens": 900, "completion_tokens": 20}
    second = {"prompt_tokens": 1000, "completion_tokens": 60}
    monkeypatch.setattr(agent_loop, "stream_chat_once", ScriptedRounds(
        [("tool_calls", [{"id": "c1", "name": "search_web", "arguments": {}}]), ("usage", first)],
        [("delta", "Found it."), ("usage", second)]))

    async def search(name, arguments, ctx):
        return "results"

    monkeypatch.setattr(builtin_tools, "call_builtin_tool", search)

    class Context:
        may_use_host_tools = True
        events = None

    async def collect():
        return [chunk async for chunk in agent_loop.run_chat_with_tools(
            "http://model", None, "m", HELLO, None, None, set(), {"search_web"}, Context())]

    events = [json.loads(chunk.decode()[len("data:"):]) for chunk in asyncio.run(collect())]
    assert [event for event in events if "usage" in event or "tool_start" in event] == [
        {"usage": first}, {"tool_start": "search_web"}, {"usage": second}]
    assert events[-1] == {"done": True}
