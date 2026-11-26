import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import remote_rules
import test_app_keys
from test_app_keys import ARTY_DECLINE, make_key, ruled_ask

clock = test_app_keys.clock
servers = test_app_keys.servers
app = test_app_keys.app
admin = test_app_keys.admin
friend = test_app_keys.friend
arty_rules = test_app_keys.arty_rules

HARMFUL = "the request Arty would decline"


class PickyArty:

    def __init__(self):
        self.asked = []

    async def __call__(self, method, path, json_body=None, **_):
        self.asked.append(json_body)
        declined = ARTY_DECLINE if HARMFUL in json_body.get("said", "") else None
        left_out = [index for index, exchange in enumerate(json_body.get("answered", []))
                    if HARMFUL in exchange["asked"] + exchange["answer"]]
        return {"left_out": left_out, "system": "You are Arty.", "declined": declined, "approvals": True}


@pytest.fixture
def picky_arty(arty_rules, monkeypatch):
    fake = PickyArty()
    monkeypatch.setattr(remote_rules.arty_devices, "ask_brain", fake)
    return fake


CHECKED_SHAPES = {
    "trailing empty user message": [{"role": "user", "content": HARMFUL}, {"role": "user", "content": ""}],
    "tool message after a user hello": [{"role": "user", "content": "hello"},
                                        {"role": "tool", "content": HARMFUL, "tool_call_id": "x"}],
    "tool result after a tool call": [
        {"role": "user", "content": "look this up"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "x", "type": "function", "function": {"name": "search_web", "arguments": "{}"}}]},
        {"role": "tool", "content": HARMFUL, "tool_call_id": "x"}],
}
CLOSED_SHAPES = {
    "trailing short user message": [{"role": "user", "content": HARMFUL}, {"role": "user", "content": "go on"}],
    "text part after a short user message": [{"role": "user", "content": [{"type": "text", "text": HARMFUL}]},
                                             {"role": "user", "content": [{"type": "text", "text": "go on"}]}],
    "tool round after the request": [
        {"role": "user", "content": HARMFUL},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "x", "type": "function", "function": {"name": "search_web", "arguments": "{}"}}]},
        {"role": "tool", "content": "nothing found", "tool_call_id": "x"}, {"role": "user", "content": "go on"}],
}

REFUSED_SHAPES = {
    "role User": [{"role": "User", "content": HARMFUL}],
    "role human": [{"role": "human", "content": HARMFUL}],
    "role System": [{"role": "System", "content": HARMFUL}, {"role": "user", "content": "do what the system says"}],
    "input_text part": [{"role": "user", "content": [{"type": "input_text", "text": HARMFUL}]}],
    "part without a type": [{"role": "user", "content": [{"text": HARMFUL}]}],
    "image part": [{"role": "user", "content": [{"type": "text", "text": "what is this"},
                                                {"type": "image_url", "image_url": {"url": "data:,"}}]}],
}


def reached_the_model(servers) -> bool:
    return any(HARMFUL in json.dumps(chat["body"]) for chat in servers.chats)


@pytest.mark.parametrize("shape", CHECKED_SHAPES)
def test_every_message_since_the_last_reply_goes_through_artys_check(app, admin, friend, servers, picky_arty, shape):
    answered = ruled_ask(app, make_key(admin)["key"], messages=CHECKED_SHAPES[shape])
    assert answered.status_code == 200, answered.text
    assert ARTY_DECLINE in answered.text
    assert not reached_the_model(servers)


@pytest.mark.parametrize("shape", CLOSED_SHAPES)
def test_an_unanswered_request_before_the_newest_message_is_left_out_not_repeated(app, admin, friend, servers,
                                                                                  picky_arty, shape):
    answered = ruled_ask(app, make_key(admin)["key"], messages=CLOSED_SHAPES[shape])
    assert answered.status_code == 200, answered.text
    assert ARTY_DECLINE not in answered.text
    assert HARMFUL not in picky_arty.asked[0]["said"]
    assert servers.chats and not reached_the_model(servers)
    assert remote_rules.LEFT_OUT_NOTE in json.dumps(servers.chats[-1]["body"])


@pytest.mark.parametrize("shape", REFUSED_SHAPES)
def test_roles_and_parts_arty_cannot_read_are_refused_on_a_ruled_connection(app, admin, friend, servers, picky_arty,
                                                                            shape):
    answered = ruled_ask(app, make_key(admin)["key"], messages=REFUSED_SHAPES[shape])
    assert answered.status_code == 400, answered.text
    assert answered.json()["error"]["type"] == "invalid_request_error"
    assert picky_arty.asked == []
    assert not reached_the_model(servers)


def test_a_conversation_arty_accepts_still_reaches_the_model(app, admin, friend, servers, picky_arty):
    answered = ruled_ask(app, make_key(admin)["key"], messages=[
        {"role": "developer", "content": "Answer briefly."}, {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Hi."}, {"role": "user", "content": [{"type": "text", "text": "and otters?"}]}])
    assert answered.status_code == 200, answered.text
    assert picky_arty.asked[-1]["said"] == "and otters?\nAnswer briefly."
    assert picky_arty.asked[-1]["answered"] == [{"asked": "hello", "answer": "Hi."}]
    assert servers.chats
