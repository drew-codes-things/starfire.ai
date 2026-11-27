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


MAIN = "hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-IQ4_XS"


def test_qwen3_is_asked_not_to_think():
    assert helper_tasks.helper_think("qwen3:14b") is False
    assert helper_tasks.helper_think("llama3.1:8b") is None


def test_every_qwen3_family_model_is_asked_not_to_think_whatever_its_name_looks_like():
    for name in (MAIN, "hf.co/owner/Qwen3.8-27B-GGUF:UD-Q5_K_M", "QWEN3:8B", "arty/qwen3.6:35b", "library/qwen3:14b"):
        assert helper_tasks.helper_think(name) is False, name
    for name in ("llama3.1:8b", "hf.co/owner/mistral-gguf:q4", "qwen2.5vl:7b"):
        assert helper_tasks.helper_think(name) is None, name


def test_helper_jobs_on_a_model_that_can_think_get_room_for_it():
    assert helper_tasks.helper_options(MAIN)["num_predict"] == helper_tasks.THINKING_HELPER_MAX_TOKENS
    assert helper_tasks.helper_options("llama3.1:8b")["num_predict"] == helper_tasks.HELPER_MAX_TOKENS
    assert helper_tasks.THINKING_HELPER_MAX_TOKENS > helper_tasks.HELPER_MAX_TOKENS


def test_options_leave_the_window_to_the_runner_settings():
    assert helper_tasks.helper_options("llama3.1:8b") == {"temperature": helper_tasks.HELPER_TEMPERATURE,
                                                          "num_predict": helper_tasks.HELPER_MAX_TOKENS}


def test_thinking_is_removed_from_replies():
    assert helper_tasks.clean_reply("<think>\nhmm\n</think>\n\nPlex setup") == "Plex setup"
    assert helper_tasks.clean_reply("  plain  ") == "plain"


def test_ollama_payload_carries_think(monkeypatch):
    import asyncio
    import chat_stream

    sent = {}

    async def fake_iter(client, url, headers, payload, max_bytes=None):
        sent.update(payload)
        yield ("delta", "ok")

    monkeypatch.setattr(chat_stream, "_iter_ollama", fake_iter)

    async def collect(model="qwen3:14b", **kwargs):
        return [item async for item in chat_stream.stream_chat_once(
            "http://127.0.0.1:11434", None, model, [{"role": "user", "content": "hi"}], None, None, **kwargs)]

    assert asyncio.run(collect(think=True)) == [("delta", "ok")]
    assert sent["think"] is True
    for thinker in ("qwen3:14b", MAIN):
        sent.clear()
        asyncio.run(collect(thinker))
        assert sent["think"] is False, thinker
    sent.clear()
    asyncio.run(collect("llama3.1:8b"))
    assert "think" not in sent
