import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import helper_tasks


def endpoint(provider: str, endpoint_id: str):
    return SimpleNamespace(id=endpoint_id, provider=provider)


def test_ollama_endpoint_is_found():
    endpoints = [endpoint("openai", "arty"), endpoint("ollama", "local")]
    assert helper_tasks.ollama_endpoint(endpoints).id == "local"
    assert helper_tasks.ollama_endpoint([endpoint("openai", "arty")]) is None


def test_qwen3_is_asked_not_to_think():
    assert helper_tasks.helper_think("qwen3:14b") is False
    assert helper_tasks.helper_think("llama3.1:8b") is None


def test_options_match_the_loaded_context():
    assert helper_tasks.helper_options(12288)["num_ctx"] == 12288
    assert "num_ctx" not in helper_tasks.helper_options(0)


def test_thinking_is_removed_from_replies():
    assert helper_tasks.clean_reply("<think>\nhmm\n</think>\n\nPlex setup") == "Plex setup"
    assert helper_tasks.clean_reply("  plain  ") == "plain"


def test_ollama_payload_carries_think(monkeypatch):
    import asyncio
    import chat_stream

    sent = {}

    async def fake_iter(client, url, headers, payload):
        sent.update(payload)
        yield ("delta", "ok")

    monkeypatch.setattr(chat_stream, "_iter_ollama", fake_iter)

    async def collect(**kwargs):
        return [item async for item in chat_stream.stream_chat_once(
            "http://127.0.0.1:11434", None, "qwen3:14b", [{"role": "user", "content": "hi"}], None, None, **kwargs)]

    assert asyncio.run(collect(think=False)) == [("delta", "ok")]
    assert sent["think"] is False
    sent.clear()
    asyncio.run(collect())
    assert "think" not in sent
