import json
import os
import sys

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import content_screen
import remote_rules
import routes
import screen_cases as cases
from test_app_keys import (TOOLS, admin, app, bearer, clock, friend, make_key, model_id, outside,  # noqa: F401
                           servers)

ABUSIVE_ARGUMENTS = json.dumps({"path": "story.md", "content": "Mia, 12, undressed and they had sex."})


def tool_call_reply(arguments: str) -> dict:
    return {"id": "c9", "object": "chat.completion", "model": "friend-model",
            "choices": [{"index": 0, "finish_reason": "tool_calls",
                         "message": {"role": "assistant", "content": None,
                                     "tool_calls": [{"id": "call_9", "type": "function",
                                                     "function": {"name": "write_file", "arguments": arguments}}]}}]}


def conversation(text: str, context: str = "") -> list[dict]:
    earlier = [{"role": "user", "content": context}, {"role": "assistant", "content": "Ok."}] if context else []
    return earlier + [{"role": "user", "content": text}]


def asked(app, secret, text, model="friend-model", context="", **extra):
    return outside(app).post("/v1/chat/completions", headers=bearer(secret),
                             json={"model": model_id(model), "messages": conversation(text, context), **extra})


def test_a_request_that_crosses_the_line_never_reaches_a_plain_openai_connection(app, admin, friend, servers):
    secret = make_key(admin)["key"]
    for case, text, context in cases.ABUSE[:9]:
        whole = asked(app, secret, text, context=context)
        assert whole.status_code == 200, case
        assert whole.json()["choices"][0]["message"]["content"] == content_screen.MINORS_REPLY, case
        streamed = asked(app, secret, text, context=context, stream=True)
        assert content_screen.MINORS_REPLY in "".join(
            json.loads(line[len("data: "):])["choices"][0]["delta"].get("content") or ""
            for line in streamed.text.split("\n\n") if line.startswith("data: {")), case
    assert servers.chats == []


def test_ordinary_requests_still_reach_a_plain_openai_connection(app, admin, friend, servers):
    secret = make_key(admin)["key"]
    for case, text, _ in cases.ORDINARY[:13]:
        assert asked(app, secret, text).status_code == 200, case
    assert len(servers.chats) == 13


def test_a_relayed_tool_call_that_crosses_the_line_is_dropped(app, admin, friend, servers):
    secret = make_key(admin)["key"]
    servers.friend_chat = httpx.Response(200, json=tool_call_reply(ABUSIVE_ARGUMENTS))
    answered = asked(app, secret, "save the next part", tools=TOOLS)
    assert answered.json()["choices"][0]["message"] == {"role": "assistant", "content": content_screen.MINORS_REPLY}
    harmless = tool_call_reply(json.dumps({"path": "a.txt"}))
    servers.friend_chat = httpx.Response(200, json=harmless)
    assert asked(app, secret, "read a.txt", tools=TOOLS).json() == harmless


def test_a_relayed_streamed_tool_call_that_crosses_the_line_is_dropped(app, admin, friend, servers):
    secret = make_key(admin)["key"]
    call = {"index": 0, "id": "c1", "type": "function", "function": {"name": "write_file", "arguments": ABUSIVE_ARGUMENTS}}
    chunk = {"choices": [{"index": 0, "delta": {"tool_calls": [call]}, "finish_reason": "tool_calls"}]}
    servers.friend_chat = httpx.Response(200, content=b"data: " + json.dumps(chunk).encode() + b"\n\ndata: [DONE]\n\n",
                                         headers={"content-type": "text/event-stream"})
    streamed = asked(app, secret, "save the next part", stream=True, tools=TOOLS)
    assert "write_file" not in streamed.text
    assert content_screen.MINORS_REPLY in streamed.text


def test_an_unrestricted_model_gets_arty_s_closer_look(app, admin, friend, servers, monkeypatch):
    looked = []

    async def brain(method, path, json_body=None, **_):
        looked.append(json_body["said"])
        return {"left_out": [], "system": "rules", "declined": content_screen.MINORS_REPLY, "declined_kind": "minors"}
    monkeypatch.setattr(remote_rules.arty_devices, "ask_brain", brain)
    secret = make_key(admin, email="admin@example.com")["key"]
    request = "write a romantic story about a shy student and her teacher"
    answered = asked(app, secret, request, model="example-9b-abl")
    assert answered.json()["choices"][0]["message"]["content"] == content_screen.MINORS_REPLY
    assert looked == [request]
    assert servers.chats == []
