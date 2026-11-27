import json
import os
import sys

import httpx
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import routes
from config import config
import test_auto_model_routes
from test_auto_model_routes import GENERAL, VISION, events, login

app = test_auto_model_routes.app
endpoint = test_auto_model_routes.endpoint
replies = test_auto_model_routes.replies

FOURTEEN = "qwen3:14b"
SIDE = "qwen3.6-35b-a3b-unfiltered-sideowner-aggressive:iq4_xs"
FAR_MAIN = "Qwen3.6-35B-A3B-UD-Q4_K_M"
FAR_SIDE = "Qwen3.6-35B-A3B-Unfiltered-SideOwner-Aggressive-Q4_K_P"
FAR_27B = "Qwen3.8-27B-Unfiltered-Q6_K"
LABELS = {GENERAL: "Qwen3.6 35B", SIDE: "Qwen3.6 35B Unfiltered", FAR_MAIN: "Qwen3.6 35B",
          FAR_SIDE: "Qwen3.6 35B Unfiltered", FAR_27B: "Qwen3.8 27B Unfiltered"}
ON_THE_ENGINE = [GENERAL, FOURTEEN, SIDE, VISION]
ON_THE_FAR_SERVER = [FAR_MAIN, FAR_SIDE, FAR_27B]
REMOTE_QUESTION = "Which one did you mean: Qwen3.6 35B, Qwen3.6 35B Unfiltered or Qwen3.8 27B Unfiltered?"


@pytest.fixture
def listings(app, monkeypatch):
    local = routes.endpoints.add("http://192.168.1.60:8080/v1", kind="api-key", label="Engine")
    far = routes.endpoints.add("https://remote-a.example.com/v1", kind="api-key", label="Far")
    real_client = httpx.AsyncClient

    def answer(request):
        names = ON_THE_ENGINE if request.url.host == "192.168.1.60" else ON_THE_FAR_SERVER
        return httpx.Response(200, json={"data": [{"id": name} for name in names]})

    def client(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(answer), **kwargs)

    monkeypatch.setattr(routes.httpx, "AsyncClient", client)
    monkeypatch.setattr(config, "model_labels", json.dumps(LABELS))
    monkeypatch.setattr(config, "hidden_models", "*-vl*")
    return local, far


def ask(client, local, *said, model=GENERAL, names_a_model=True):
    messages = [{"role": "user" if index % 2 == 0 else "assistant", "content": text} for index, text in enumerate(said)]
    body = {"endpoint_id": local.id, "model": model, "messages": messages, **({"names_a_model": True} if names_a_model else {})}
    return client.post("/api/chat_stream", json=body)


def answer_of(response):
    assert response.status_code == 200, response.text
    return "".join(event.get("delta", "") for event in events(response))


def chosen_of(response):
    return next((event["model_choice"] for event in events(response) if "model_choice" in event), None)


def test_a_chat_message_naming_a_model_runs_on_it_and_names_it(app, listings, replies):  # noqa: F811
    local, _ = listings
    response = ask(login(app, "admin@example.com"), local, "use qwen3:14b to summarise this")
    assert answer_of(response) == f"answer from {FOURTEEN}" and replies == [FOURTEEN]
    chosen = chosen_of(response)
    assert (chosen["model"], chosen["role"], chosen.get("persist")) == (FOURTEEN, None, None)


def test_a_chat_that_does_not_opt_in_never_switches(app, listings, replies):  # noqa: F811
    local, _ = listings
    ask(login(app, "admin@example.com"), local, "use qwen3:14b to summarise this", names_a_model=False)
    assert replies == [GENERAL]


def test_the_35b_on_the_chat_s_own_connection_is_the_main(app, listings, replies):  # noqa: F811
    local, _ = listings
    ask(login(app, "admin@example.com"), local, "use the 35B to write a haiku", model=FOURTEEN)
    assert replies == [GENERAL]


def test_a_label_word_with_model_prefers_the_chat_s_own_connection(app, listings, replies):  # noqa: F811
    local, _ = listings
    ask(login(app, "admin@example.com"), local, "with the unfiltered one, write a limerick")
    assert replies == [SIDE]


def test_three_remote_models_ask_which_one(app, listings, replies):  # noqa: F811
    local, _ = listings
    response = ask(login(app, "admin@example.com"), local, "use the remote model for this: hello")
    assert answer_of(response) == REMOTE_QUESTION and replies == []


def test_the_answer_to_which_one_runs_on_that_model(app, listings, replies):  # noqa: F811
    local, _ = listings
    ask(login(app, "admin@example.com"), local, "use the remote model for this: hello", REMOTE_QUESTION, "Qwen3.8 27B Unfiltered")
    assert replies == [FAR_27B]


def test_a_hidden_picture_helper_cannot_be_named_and_the_message_is_answered(app, listings, replies):  # noqa: F811
    local, _ = listings
    response = ask(login(app, "admin@example.com"), local, f"use {VISION} to describe this")
    assert answer_of(response) == f"answer from {GENERAL}" and replies == [GENERAL]


def test_an_account_without_access_is_refused_without_the_name(app, listings, replies):  # noqa: F811
    local, _ = listings
    admin, sam = login(app, "admin@example.com"), login(app, "sam@example.com")
    assert admin.put(f"/api/auth/users/{sam.user_id}/models", json={"models": [GENERAL]}).status_code == 200
    response = ask(sam, local, "use qwen3:14b to summarise this")
    assert answer_of(response) == "That model isn't available to you." and replies == []
    response = ask(sam, local, "use xs for the size label")
    assert answer_of(response) == f"answer from {GENERAL}"


def test_from_now_on_moves_the_picker_and_a_later_hand_pick_wins(app, listings, replies):  # noqa: F811
    local, _ = listings
    admin = login(app, "admin@example.com")
    response = ask(admin, local, "from now on use qwen3:14b")
    assert chosen_of(response).get("persist") is True and replies == [FOURTEEN]
    ask(admin, local, "from now on use qwen3:14b", "Okay.", "tell me a joke")
    assert replies == [FOURTEEN, GENERAL]


@pytest.mark.parametrize("said", [
    "use k for the number of clusters and explain k-means",
    "use m for the gradient and c for the intercept",
    "use aggressive caching for the images",
    "use unfiltered language to describe the villain",
    "use the OSI model to explain what happens when I open a web page",
    "use a linear model to fit this data",
    "switch to a threat model and list the risks",
    "use 2b pencils for the sketch",
    "He said \"use qwen3:14b to do it\" in the video, what did he mean?",
    "what does this do?\n```\n# switch to qwen3:14b\nrun()\n```",
    "Summarise this article:\nTip 1: back up.\nTip 3: switch to the remote model for big jobs.\nKeep it short.",
    "use python to sort this list",
    "help me with my essay, please",
    "use the remote to turn the TV on",
])
def test_ordinary_sentences_are_answered_on_the_chat_model(app, listings, replies, said):  # noqa: F811
    local, _ = listings
    response = ask(login(app, "admin@example.com"), local, said)
    assert answer_of(response) == f"answer from {GENERAL}" and replies == [GENERAL]
