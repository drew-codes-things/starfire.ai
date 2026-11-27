import asyncio
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import routes
from config import config
from model_choice import ModelChoice, ModelChoiceStore
import test_chat_routing
from test_chat_routing import login

app = test_chat_routing.app

pytestmark = pytest.mark.usefixtures("unreadable_model_lists")

BIG = "hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-IQ4_XS"
SIDE = "hf.co/maker/Qwen3.6-35B-A3B-Side-GGUF:IQ4_XS"
HELPER = "qwen2.5vl:7b"
SMALL = "qwen3:14b"
LABELS = {BIG: "Qwen3.6 35B", SIDE: "Qwen3.6 35B Side", "remote-35b-q4": "Remote 35B"}


@pytest.fixture
def listed(app, monkeypatch):  # noqa: F811
    engine = routes.endpoints.add("http://127.0.0.1:11438", kind="ollama")
    brain = routes.endpoints.add("https://brain.example.com/v1", kind="api-key")
    models = [{"id": name, "endpoint_id": engine.id, "provider": "ollama"} for name in (BIG, SIDE, SMALL, HELPER)]
    models += [{"id": name, "endpoint_id": brain.id, "provider": "openai"}
               for name in ("Arty", f"arty/{BIG}", f"arty/{SIDE}")]

    async def listing():
        return models, {}

    monkeypatch.setattr(routes, "_model_listing", listing)
    monkeypatch.setattr(config, "own_tools_models", "Arty,arty/*")
    monkeypatch.setattr(config, "model_labels", json.dumps(LABELS))
    monkeypatch.setattr(config, "hidden_models", f"{HELPER},{SIDE}")
    return engine, brain


def shown(client):
    return {(m["endpoint_id"], m["id"]): m for m in client.get("/api/models").json()["models"]}


def test_listed_models_carry_their_label_and_arty_s_copies_use_the_model_s_label(app, listed):  # noqa: F811
    engine, brain = listed
    models = shown(login(app, "admin@example.com"))
    assert models[(engine.id, BIG)]["label"] == "Qwen3.6 35B"
    assert models[(brain.id, f"arty/{SIDE}")]["label"] == "Qwen3.6 35B Side"
    assert "label" not in models[(engine.id, SMALL)] and "label" not in models[(brain.id, "Arty")]


def test_hidden_models_never_reach_the_picker_but_arty_s_copy_of_one_stays(app, listed):  # noqa: F811
    engine, brain = listed
    models = shown(login(app, "admin@example.com"))
    assert (engine.id, HELPER) not in models and (engine.id, SIDE) not in models
    assert (brain.id, f"arty/{SIDE}") in models and (engine.id, SMALL) in models


def test_a_hidden_picture_helper_still_describes_the_screen_for_a_blind_model(app, listed, monkeypatch):  # noqa: F811
    engine, _ = listed

    async def sees(endpoint, model):
        return model == HELPER

    monkeypatch.setattr(routes, "_engine_says_it_sees", sees)
    seeing_endpoint, seeing_model = asyncio.run(routes._screen_viewer(engine, SMALL))
    assert (seeing_endpoint.id, seeing_model) == (engine.id, HELPER)


def test_settings_that_do_not_parse_change_nothing(app, listed, monkeypatch):  # noqa: F811
    engine, _ = listed
    monkeypatch.setattr(config, "model_labels", "{not json")
    monkeypatch.setattr(config, "hidden_models", "")
    models = shown(login(app, "admin@example.com"))
    assert (engine.id, HELPER) in models and "label" not in models[(engine.id, BIG)]
    monkeypatch.setattr(config, "model_labels", '["a list"]')
    assert config.labels() == {}


@pytest.fixture
def renamed(listed, monkeypatch):
    monkeypatch.setattr(config, "model_renames", json.dumps({"own/Old": f"arty/{SIDE}"}))
    return listed


def test_a_stored_choice_of_a_renamed_model_reads_as_its_new_name(app, renamed):  # noqa: F811
    _, brain = renamed
    admin = login(app, "admin@example.com")
    assert admin.put("/api/model-choice", json={"endpoint_id": brain.id, "model": "own/Old"}).status_code == 200
    assert admin.get("/api/model-choice").json() == {"endpoint_id": brain.id, "model": f"arty/{SIDE}"}
    assert config.renamed("Arty") == "Arty"


def test_a_chat_kept_on_a_renamed_model_opens_on_its_new_name(app, renamed):  # noqa: F811
    _, brain = renamed
    admin = login(app, "admin@example.com")
    session = admin.post("/api/chat/sessions", json={"title": "old"}).json()
    admin.put(f"/api/chat/sessions/{session['id']}", json={"endpoint_id": brain.id, "model": "own/Old"})
    assert admin.get(f"/api/chat/sessions/{session['id']}").json()["model"] == f"arty/{SIDE}"


def test_a_message_sent_to_a_renamed_model_goes_to_its_new_name(app, renamed, monkeypatch):  # noqa: F811
    _, brain = renamed
    asked = []

    async def plain(base_url, api_key, model, messages, system, options):
        asked.append(model)
        yield routes._sse_line({"done": True})

    monkeypatch.setattr(routes, "stream_chat", plain)
    routes.api_keys.save(brain.id, "sk-PLACEHOLDER")
    admin = login(app, "admin@example.com")
    response = admin.post("/api/chat_stream", json={"endpoint_id": brain.id, "model": "own/Old",
                                                    "messages": [{"role": "user", "content": "hi"}]})
    assert response.status_code == 200 and asked == [f"arty/{SIDE}"]


def test_no_stored_choice_reads_as_nothing_chosen_so_the_picker_can_start_on_arty(app, listed):  # noqa: F811
    admin = login(app, "admin@example.com")
    assert admin.get("/api/model-choice").json() == {"endpoint_id": "", "model": ""}
    assert admin.put("/api/model-choice", json={"endpoint_id": "auto", "model": "auto"}).json()["model"] == "auto"
    assert admin.get("/api/model-choice").json() == {"endpoint_id": "auto", "model": "auto"}


def test_the_store_tells_a_kept_auto_from_no_choice(tmp_path):
    store = ModelChoiceStore(str(tmp_path))
    assert store.stored() is None
    store.set(ModelChoice())
    assert store.stored() == ModelChoice()


def test_the_main_model_on_the_engine_is_listed_with_its_fixed_context(app, listed, monkeypatch):  # noqa: F811
    engine, brain = listed
    monkeypatch.setattr(config, "task_model", BIG)
    monkeypatch.setattr(config, "task_num_ctx", 24576)
    models = shown(login(app, "admin@example.com"))
    assert models[(engine.id, BIG)]["fixed_context"] == 24576
    assert "fixed_context" not in models[(engine.id, SMALL)]
    assert "fixed_context" not in models[(brain.id, f"arty/{BIG}")]


def test_no_fixed_context_when_the_window_is_left_to_the_engine(app, listed, monkeypatch):  # noqa: F811
    engine, _ = listed
    monkeypatch.setattr(config, "task_model", BIG)
    monkeypatch.setattr(config, "task_num_ctx", 0)
    assert "fixed_context" not in shown(login(app, "admin@example.com"))[(engine.id, BIG)]


@pytest.fixture
def side_model(listed, monkeypatch):
    monkeypatch.setattr(config, "hidden_models", HELPER)
    monkeypatch.setattr(config, "model_labels", json.dumps({BIG: "Qwen3.6 35B"}))
    monkeypatch.setattr(config, "model_renames", "")
    monkeypatch.setattr(config, "side_model", SIDE)
    monkeypatch.setattr(config, "side_model_label", "Qwen3.6 35B Side")
    return listed


def test_side_model_hides_its_raw_id_and_labels_arty_s_copy(app, side_model):  # noqa: F811
    engine, brain = side_model
    models = shown(login(app, "admin@example.com"))
    assert (engine.id, SIDE) not in models and (engine.id, HELPER) not in models
    assert models[(brain.id, f"arty/{SIDE}")]["label"] == "Qwen3.6 35B Side"
    assert models[(engine.id, BIG)]["label"] == "Qwen3.6 35B"


def test_a_rename_can_name_the_side_model_without_repeating_its_id(app, side_model, monkeypatch):  # noqa: F811
    _, brain = side_model
    monkeypatch.setattr(config, "model_renames", json.dumps({"own/Old": "arty/{SIDE_MODEL}", "own/Gone": "Arty"}))
    assert config.renamed("own/Old") == f"arty/{SIDE}" and config.renamed("own/Gone") == "Arty"
    admin = login(app, "admin@example.com")
    admin.put("/api/model-choice", json={"endpoint_id": brain.id, "model": "own/Old"})
    assert admin.get("/api/model-choice").json() == {"endpoint_id": brain.id, "model": f"arty/{SIDE}"}


def test_a_voice_rule_can_name_the_side_model_without_repeating_its_id(side_model, monkeypatch):
    monkeypatch.setattr(config, "model_voices", "arty/{SIDE_MODEL}=side-voice.onnx@1.1")
    [rule] = routes._voice_rules()
    assert routes.model_voices.rule_for(f"arty/{SIDE}", [rule]) is rule
    assert routes.model_voices.rule_for("arty/other", [rule]) is None


def test_an_explicit_label_still_wins_over_side_model_label(side_model, monkeypatch):
    monkeypatch.setattr(config, "model_labels", json.dumps({SIDE: "Named In Labels"}))
    assert config.labels()[SIDE] == "Named In Labels"


def test_no_side_model_adds_nothing_and_leaves_placeholder_renames_alone(listed, monkeypatch):
    monkeypatch.setattr(config, "hidden_models", HELPER)
    monkeypatch.setattr(config, "model_labels", "")
    monkeypatch.setattr(config, "model_renames", json.dumps({"own/Old": "arty/{SIDE_MODEL}"}))
    monkeypatch.setattr(config, "side_model", "")
    monkeypatch.setattr(config, "side_model_label", "Ignored Without An Id")
    assert config.hidden_model_patterns() == [HELPER]
    assert config.labels() == {}
    assert config.renamed("own/Old") == "own/Old"


def test_side_model_and_its_label_are_documented_in_the_example_env():
    example = open(os.path.join(ROOT, ".env.example"), encoding="utf-8").read().splitlines()
    assert "SIDE_MODEL=" in example and "SIDE_MODEL_LABEL=" in example
    docs = open(os.path.join(ROOT, "docs", "configuration.md"), encoding="utf-8").read()
    assert "`SIDE_MODEL`" in docs and "`SIDE_MODEL_LABEL`" in docs
