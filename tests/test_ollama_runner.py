import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import pytest

import chat_stream
from config import AppConfig, config

BRAIN_WINDOW = 24576
OLLAMA_URL = "http://127.0.0.1:11434"
OPENAI_URL = "http://brain:8081/v1"


@pytest.fixture
def sent(monkeypatch):
    captured = {}

    async def fake_iter(client, url, headers, payload):
        captured.update(payload)
        yield ("delta", "ok")

    monkeypatch.setattr(chat_stream, "_iter_ollama", fake_iter)
    monkeypatch.setattr(chat_stream, "_iter_openai", fake_iter)
    monkeypatch.setattr(config, "task_num_ctx", BRAIN_WINDOW)
    return captured


def send(base_url: str, options: dict | None):
    async def collect():
        return [item async for item in chat_stream.stream_chat_once(
            base_url, None, "qwen3:14b", [{"role": "user", "content": "hi"}], None, options)]

    return asyncio.run(collect())


def test_the_runner_window_defaults_to_the_brains(monkeypatch):
    monkeypatch.delenv("TASK_NUM_CTX", raising=False)
    assert AppConfig(_env_file=None).task_num_ctx == BRAIN_WINDOW


def test_an_ollama_chat_asks_for_the_brains_window_without_context_shift(sent):
    send(OLLAMA_URL, None)
    assert sent["options"] == {"num_ctx": BRAIN_WINDOW}
    assert sent["shift"] is False


def test_an_ollama_chat_keeps_its_other_options(sent):
    send(OLLAMA_URL, {"temperature": 0.3, "num_predict": 200})
    assert sent["options"] == {"temperature": 0.3, "num_predict": 200, "num_ctx": BRAIN_WINDOW}
    assert sent["shift"] is False


def test_a_context_length_chosen_for_the_chat_is_kept(sent):
    send(OLLAMA_URL, {"num_ctx": 4096})
    assert sent["options"] == {"num_ctx": 4096}
    assert sent["shift"] is False


def test_a_zero_window_leaves_the_size_to_ollama_but_still_turns_shift_off(sent, monkeypatch):
    monkeypatch.setattr(config, "task_num_ctx", 0)
    send(OLLAMA_URL, None)
    assert "options" not in sent
    assert sent["shift"] is False


def test_other_providers_are_not_sent_ollama_runner_fields(sent):
    send(OPENAI_URL, {"num_ctx": 4096})
    assert "shift" not in sent
    assert "num_ctx" not in sent
