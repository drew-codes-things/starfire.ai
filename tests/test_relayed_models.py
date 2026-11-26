import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server_configs"))

from connection_health import models_in_answer

ENGINE = SimpleNamespace(id="engine1", provider="ollama")
OPENAI = SimpleNamespace(id="friend1", provider="openai")


def test_an_engine_connection_does_not_list_the_remote_copies_it_publishes():
    answer = {"models": [{"name": "qwen3:14b"}, {"name": "remote/starfire/qwen3-14b"},
                         {"name": "remote/friend/some-model"}, {"name": "mimo-v2.6:9b"}]}
    ids = [model["id"] for model in models_in_answer(ENGINE, answer)]
    assert ids == ["qwen3:14b", "mimo-v2.6:9b"]


def test_a_gateway_connection_keeps_a_model_whose_name_starts_with_remote():
    answer = {"data": [{"id": "remote/model"}, {"id": "plain"}]}
    ids = [model["id"] for model in models_in_answer(OPENAI, answer)]
    assert ids == ["remote/model", "plain"]


def test_names_that_only_look_like_remote_copies_stay_listed_on_an_engine_connection():
    near_misses = ["remote:latest", "remote-control:7b", "hf.co/remote/x:Q4"]
    answer = {"models": [{"name": name} for name in near_misses + ["remote/starfire/qwen3-14b"]]}
    ids = [model["id"] for model in models_in_answer(ENGINE, answer)]
    assert ids == near_misses
