import asyncio
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import auth_routes
import chat_stream
import remote_rules
import routes
import test_app_keys
import test_remote_rules
import user_data
from test_app_keys import make_key, ruled_ask
from test_remote_rules import FRIEND_MODEL, chat_messages, login

clock = test_app_keys.clock
servers = test_app_keys.servers
app = test_app_keys.app
admin = test_app_keys.admin
friend = test_app_keys.friend
arty_rules = test_app_keys.arty_rules
connections = test_remote_rules.connections

OLD_MARK = "OLD"
HISTORY = [{"role": "system", "content": "be brief"}, {"role": "user", "content": f"{OLD_MARK} question"},
           {"role": "assistant", "content": "old answer"}, {"role": "user", "content": "hello"},
           {"role": "assistant", "content": "Hi."}, {"role": "user", "content": "new question"}]
LEFT_OUT_TEXTS = (f"{OLD_MARK} question", "old answer")
MISSING = "missing"


class LeavingOutBrain:
    def __init__(self):
        self.asked = []
        self.left_out = None

    async def __call__(self, method, path, json_body=None, **_):
        self.asked.append(json_body)
        answer = {"system": "You are Arty.", "declined": None, "approvals": True}
        if self.left_out == MISSING:
            return answer
        if self.left_out is not None:
            answer["left_out"] = self.left_out
        elif "answered" in json_body:
            answer["left_out"] = [index for index, exchange in enumerate(json_body["answered"])
                                  if OLD_MARK in exchange["asked"]]
        return answer


@pytest.fixture
def leaving_out(monkeypatch):
    fake = LeavingOutBrain()
    monkeypatch.setattr(remote_rules.arty_devices, "ask_brain", fake)
    return fake


def sent_texts(messages):
    return [remote_rules.text_of(message.get("content")) for message in messages]


@pytest.mark.parametrize("stream", [False, True])
def test_an_exchange_arty_leaves_out_never_reaches_the_model(app, admin, friend, servers, arty_rules, leaving_out,
                                                             stream):
    answered = ruled_ask(app, make_key(admin)["key"], stream=stream, messages=HISTORY)
    assert answered.status_code == 200, answered.text
    texts = sent_texts(servers.chats[-1]["body"]["messages"])
    assert not any(left in text for left in LEFT_OUT_TEXTS for text in texts)
    assert texts[-3:] == ["hello", "Hi.", "new question"]
    assert leaving_out.asked[-1]["said"] == "new question\nbe brief"


@pytest.mark.parametrize("left_out", [MISSING, [2], [-1], ["0"], 0, True])
def test_rules_that_do_not_say_which_exchanges_to_leave_out_are_refused(app, admin, friend, servers, arty_rules,
                                                                        leaving_out, left_out):
    leaving_out.left_out = left_out
    refused = ruled_ask(app, make_key(admin)["key"], messages=HISTORY)
    assert refused.status_code == 502, refused.text
    assert servers.chats == []


def test_a_conversation_with_no_answered_history_needs_no_left_out(app, admin, friend, servers, arty_rules,
                                                                   monkeypatch):
    async def old_brain(method, path, json_body=None, **_):
        return {"system": "You are Arty.", "declined": None, "approvals": True}
    monkeypatch.setattr(remote_rules.arty_devices, "ask_brain", old_brain)
    assert ruled_ask(app, make_key(admin)["key"]).status_code == 200


def test_the_web_chat_never_sends_an_exchange_arty_leaves_out(app, connections, leaving_out, monkeypatch):
    friend, _, _, _ = connections
    sent = []

    async def plain(base_url, api_key, model, messages, system, options):
        sent.append(messages)
        yield chat_stream._sse_line({"done": True})
    monkeypatch.setattr(routes, "stream_chat", plain)
    response = chat_messages(login(app, "admin@example.com"), friend.id, FRIEND_MODEL, HISTORY)
    assert response.status_code == 200, response.text
    texts = sent_texts(sent[-1])
    assert not any(left in text for left in LEFT_OUT_TEXTS for text in texts)
    assert texts[-3:] == ["hello", "Hi.", "new question"]


def test_a_task_run_on_a_ruled_connection_never_sends_an_exchange_arty_leaves_out(app, connections, leaving_out,
                                                                                    monkeypatch):
    friend, _, _, _ = connections
    sent = []

    async def run_chat_collected(base_url, api_key, model, messages, system=None, **settings):
        sent.append(messages)
        return "done"
    import agent_loop
    monkeypatch.setattr(agent_loop, "run_chat_collected", run_chat_collected)
    endpoint = routes.endpoints.get(friend.id)
    with user_data.run_as(auth_routes.users.find_by_email("admin@example.com").id):
        assert asyncio.run(routes._ruled_collected(endpoint, "sk-PLACEHOLDER", FRIEND_MODEL, HISTORY)) == "done"
    texts = sent_texts(sent[-1])
    assert not any(left in text for left in LEFT_OUT_TEXTS for text in texts)
    assert texts[-3:] == ["hello", "Hi.", "new question"]


def on_guard_thread(names, function):
    def recorded(*args, **kwargs):
        names.append(threading.current_thread().name)
        return function(*args, **kwargs)
    return recorded


def test_a_collected_reply_is_checked_for_arty_s_rules_off_the_event_loop(app, connections, leaving_out, monkeypatch):
    friend, _, _, _ = connections
    names = []

    async def run_chat_collected(base_url, api_key, model, messages, system=None, **settings):
        return "done"
    import agent_loop
    monkeypatch.setattr(agent_loop, "run_chat_collected", run_chat_collected)
    monkeypatch.setattr(remote_rules, "without_rules_text", on_guard_thread(names, remote_rules.without_rules_text))
    endpoint = routes.endpoints.get(friend.id)
    with user_data.run_as(auth_routes.users.find_by_email("admin@example.com").id):
        assert asyncio.run(routes._ruled_collected(endpoint, "sk-PLACEHOLDER", FRIEND_MODEL, HISTORY)) == "done"
    assert names and all(name.startswith(remote_rules.GUARD_THREAD_NAME) for name in names)


def test_every_stream_guard_check_runs_on_the_guard_threads(monkeypatch):
    names = []
    for method in ("read", "release", "calls_repeat"):
        monkeypatch.setattr(remote_rules.RulesTextGuard, method,
                            on_guard_thread(names, getattr(remote_rules.RulesTextGuard, method)))

    async def events():
        yield "delta", "Here is the plan. "
        yield "tool_calls", [{"name": "search", "arguments": "{\"query\": \"otters\"}"}]
        yield "done", None

    async def collected():
        hidden = remote_rules.rules_text(remote_rules.ArtyRules(system="Keep these words private.", declined=None))
        return [item async for item in remote_rules.without_rules_text_in_events(events(), hidden)]
    asyncio.run(collected())
    assert len(names) >= 3
    assert all(name.startswith(remote_rules.GUARD_THREAD_NAME) for name in names)


SHORT_GUARD_SECONDS = 0.1
SLOW_GUARD_SECONDS = 0.5
PRIVATE_SYSTEM = "Keep these words private."


def slow_read_after(pieces_read):
    original = remote_rules.RulesTextGuard.read
    calls = []

    def read(self, piece):
        calls.append(piece)
        if len(calls) > pieces_read:
            time.sleep(SLOW_GUARD_SECONDS)
        return original(self, piece)
    return read


def test_a_stream_check_that_takes_too_long_cuts_the_reply_with_the_normal_decline(monkeypatch):
    monkeypatch.setattr(remote_rules, "GUARD_CALL_SECONDS", SHORT_GUARD_SECONDS)
    monkeypatch.setattr(remote_rules.RulesTextGuard, "read", slow_read_after(1))

    async def events():
        yield "delta", "Here is the plan. "
        yield "delta", "More of the plan. "
        yield "delta", "And the end."
        yield "tool_calls", [{"name": "search", "arguments": "{\"query\": \"otters\"}"}]
        yield "done", None

    async def collected():
        hidden = remote_rules.rules_text(remote_rules.ArtyRules(system=PRIVATE_SYSTEM, declined=None))
        return [item async for item in remote_rules.without_rules_text_in_events(events(), hidden)]
    items = asyncio.run(collected())
    texts = "".join(text for kind, text in items if kind == "delta")
    assert texts == "Here is the plan. \n\n" + remote_rules.private_text.DECLINE
    assert [kind for kind, _ in items if kind != "delta"] == ["done"]


def test_a_slow_check_on_a_streamed_sse_reply_cuts_it_with_the_normal_decline(monkeypatch):
    monkeypatch.setattr(remote_rules, "GUARD_CALL_SECONDS", SHORT_GUARD_SECONDS)
    monkeypatch.setattr(remote_rules.RulesTextGuard, "read", slow_read_after(0))

    async def generator():
        for delta in ("Hello there. ", "More words."):
            yield remote_rules.sse_line({"delta": delta})
        yield remote_rules.sse_line({"done": True})

    async def collected():
        hidden = remote_rules.rules_text(remote_rules.ArtyRules(system=PRIVATE_SYSTEM, declined=None))
        return [chunk async for chunk in remote_rules.without_rules_text_in_stream(generator(), hidden)]
    payloads = [remote_rules.sse_payload(chunk) for chunk in asyncio.run(collected())]
    assert "".join(payload["delta"] for payload in payloads if "delta" in payload) == remote_rules.private_text.DECLINE
    assert payloads[-1] == {"done": True}
