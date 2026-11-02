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
    assert helper_tasks.helper_prompt("Title?", "qwen3:14b").endswith("/no_think")
    assert helper_tasks.helper_prompt("Title?", "llama3.1:8b") == "Title?"


def test_options_match_the_loaded_context():
    assert helper_tasks.helper_options(12288)["num_ctx"] == 12288
    assert "num_ctx" not in helper_tasks.helper_options(0)


def test_thinking_is_removed_from_replies():
    assert helper_tasks.clean_reply("<think>\nhmm\n</think>\n\nPlex setup") == "Plex setup"
    assert helper_tasks.clean_reply("  plain  ") == "plain"
