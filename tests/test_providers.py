import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server_configs"))

import providers
from config import config


def test_configured_ollama_on_another_host_is_native(monkeypatch):
    monkeypatch.setattr(config, "ollama_base_url", "http://192.168.1.13:11435")
    base = "http://192.168.1.13:11435"
    assert providers._detect_provider(base) == "ollama"
    assert providers.build_models_url(base) == "http://192.168.1.13:11435/api/tags"
    assert providers.build_chat_url(base) == "http://192.168.1.13:11435/api/chat"


def test_openai_style_paths_on_the_ollama_host_stay_openai(monkeypatch):
    monkeypatch.setattr(config, "ollama_base_url", "http://192.168.1.13:11435")
    assert providers._detect_provider("http://192.168.1.13:11435/v1") == "openai"


def test_other_hosts_are_not_treated_as_ollama(monkeypatch):
    monkeypatch.setattr(config, "ollama_base_url", "http://192.168.1.13:11435")
    assert providers._detect_provider("http://192.168.1.13:8081/v1") == "openai"
    assert providers._detect_provider("http://192.168.1.13:11436") == "openai"


def test_default_local_ollama_still_detected(monkeypatch):
    monkeypatch.setattr(config, "ollama_base_url", None)
    assert providers._detect_provider("http://localhost:11434") == "ollama"
    assert providers._detect_provider("https://api.anthropic.com") == "anthropic"


def test_embedding_models_are_recognised():
    assert providers.is_embedding_model("nomic-embed-text:latest")
    assert providers.is_embedding_model("mxbai-EMBED-large")
    assert not providers.is_embedding_model("qwen3:14b")
    assert not providers.is_embedding_model(None)
