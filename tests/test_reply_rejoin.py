import asyncio
import json
import os
import sys
import threading
import time

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import auth
import auth_routes
import chat_stream
import routes
import user_data
from api_key_manager import APIKeyManager
from auth import SessionStore, UserStore
from config import config
from generation_quota import GenerationQuota
from model_access import ModelAccessStore
from model_endpoints import ModelEndpointStore

pytestmark = pytest.mark.usefixtures("unreadable_model_lists")

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}
MODEL = "general:14b"
WAIT_SECONDS = 5
POLL_SECONDS = 0.02


def line(event):
    return chat_stream._sse_line(event)


@pytest.fixture
def app(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    users, sessions = UserStore(data_dir), SessionStore(data_dir)
    monkeypatch.setattr(config, "data_dir", data_dir)
    monkeypatch.setattr(config, "ollama_base_url", None)
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(auth_routes, "known_addresses", auth.KnownAddresses(data_dir))
    monkeypatch.setattr(routes, "registry", user_data.configure(data_dir))
    monkeypatch.setattr(routes, "api_keys", APIKeyManager(data_dir))
    monkeypatch.setattr(routes, "endpoints", ModelEndpointStore(data_dir))
    monkeypatch.setattr(routes, "model_access_store", ModelAccessStore(data_dir))
    monkeypatch.setattr(routes, "generation_quota", GenerationQuota())
    if hasattr(routes, "reply_runs"):
        monkeypatch.setattr(routes, "reply_runs", routes.ReplyRuns())
    hashed = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    users.upsert("sam@example.com", "Sam", "user", hashed)
    users.upsert("kim@example.com", "Kim", "user", hashed)
    application = FastAPI()
    auth.install(application, users, sessions)
    application.include_router(auth_routes.router)
    application.include_router(routes.router)
    return application


@pytest.fixture
def held_replies(monkeypatch):
    release = threading.Event()

    async def fake_stream(base_url, api_key, model, messages, system, options):
        yield line({"delta": "first part"})
        while not release.is_set():
            await asyncio.sleep(POLL_SECONDS)
        yield line({"delta": ", second part"})
        yield line({"done": True})

    monkeypatch.setattr(routes, "stream_chat", fake_stream)
    return release


def signed_in(client, email):
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    return client


def chat_with_question(client):
    session = client.post("/api/chat/sessions", json={"title": "q"}).json()
    question = [{"role": "user", "content": "tell me a story"}]
    client.put(f"/api/chat/sessions/{session['id']}", json={"messages": question})
    return session["id"], question


def wait_for(check):
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        found = check()
        if found:
            return found
        time.sleep(POLL_SECONDS)
    return None


def events(text):
    return [json.loads(chunk[len("data:"):]) for chunk in text.split("\n\n") if chunk.startswith("data:")]


def test_a_reply_in_a_saved_chat_is_finished_and_saved_by_the_server_and_can_be_rejoined(app, held_replies):
    endpoint = routes.endpoints.add("https://models.example.com/v1", kind="api-key")
    with TestClient(app, base_url="https://testserver", headers=HEADERS) as sam, \
            TestClient(app, base_url="https://testserver", headers=HEADERS) as kim:
        signed_in(sam, "sam@example.com")
        signed_in(kim, "kim@example.com")
        session_id, question = chat_with_question(sam)
        first_page = {}

        def first_page_asks():
            first_page["response"] = sam.post("/api/chat_stream", json={
                "endpoint_id": endpoint.id, "model": MODEL, "messages": question,
                "session_id": session_id, "reply_index": 1})

        asking = threading.Thread(target=first_page_asks)
        asking.start()
        running = wait_for(lambda: sam.get(f"/api/chat/sessions/{session_id}").json().get("running_reply"))
        assert running and running["reply_index"] == 1
        assert kim.get(f"/api/chat/replies/{running['id']}/events").status_code == 404
        second = sam.post("/api/chat_stream", json={"endpoint_id": endpoint.id, "model": MODEL, "messages": question,
                                                    "session_id": session_id, "reply_index": 1})
        assert second.status_code == 409
        held_replies.set()
        asking.join(WAIT_SECONDS)
        rejoined = sam.get(f"/api/chat/replies/{running['id']}/events")
        saved = sam.get(f"/api/chat/sessions/{session_id}").json()

    assert events(first_page["response"].text)[0] == {"reply": running}
    assert [e.get("delta") for e in events(rejoined.text) if "delta" in e] == ["first part", ", second part"]
    assert saved["running_reply"] is None
    assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]
    assert saved["messages"][1]["content"] == "first part, second part" and saved["messages"][1]["model"] == MODEL


def saved_messages(user_id, session_id):
    with user_data.run_as(user_id):
        return routes.chat_sessions.get(session_id).messages


@pytest.mark.parametrize("closing", ["disable", "delete"])
def test_a_reply_stops_without_being_saved_when_its_account_is_disabled_or_removed(app, held_replies, monkeypatch,
                                                                                   closing):
    monkeypatch.setattr(routes, "ACCOUNT_RECHECK_SECONDS", 0, raising=False)
    endpoint = routes.endpoints.add("https://models.example.com/v1", kind="api-key")
    sam_id = auth_routes.users.find_by_email("sam@example.com").id
    auth_routes.users.upsert("boss@example.com", "Boss", "admin", auth_routes.users.find_by_id(sam_id).password_hash)
    with TestClient(app, base_url="https://testserver", headers=HEADERS) as sam:
        signed_in(sam, "sam@example.com")
        session_id, question = chat_with_question(sam)
        answer = {}

        def ask():
            answer["response"] = sam.post("/api/chat_stream", json={
                "endpoint_id": endpoint.id, "model": MODEL, "messages": question,
                "session_id": session_id, "reply_index": 1})

        asking = threading.Thread(target=ask)
        asking.start()
        assert wait_for(lambda: sam.get(f"/api/chat/sessions/{session_id}").json().get("running_reply"))
        if closing == "disable":
            auth_routes.users.update(sam_id, disabled=True)
        else:
            auth_routes.users.delete(sam_id)
        held_replies.set()
        asking.join(WAIT_SECONDS)

    streamed = events(answer["response"].text)
    assert ", second part" not in [event.get("delta") for event in streamed]
    assert routes.CLOSED_ACCOUNT_STOPPED_REPLY in [event.get("error") for event in streamed]
    if closing == "disable":
        assert [message["role"] for message in saved_messages(sam_id, session_id)] == ["user"]


def test_a_stopped_reply_is_not_saved_by_the_server(app, held_replies):
    endpoint = routes.endpoints.add("https://models.example.com/v1", kind="api-key")
    sam_id = auth_routes.users.find_by_email("sam@example.com").id
    with TestClient(app, base_url="https://testserver", headers=HEADERS) as sam:
        signed_in(sam, "sam@example.com")
        session_id, question = chat_with_question(sam)
        answer = {}

        def ask():
            answer["response"] = sam.post("/api/chat_stream", json={
                "endpoint_id": endpoint.id, "model": MODEL, "messages": question,
                "session_id": session_id, "reply_index": 1})

        asking = threading.Thread(target=ask)
        asking.start()
        running = wait_for(lambda: sam.get(f"/api/chat/sessions/{session_id}").json().get("running_reply"))
        assert sam.post(f"/api/chat/replies/{running['id']}/stop").json() == {"stopped": True}
        asking.join(WAIT_SECONDS)
        assert wait_for(lambda: sam.get(f"/api/chat/sessions/{session_id}").json().get("running_reply") is None)

    assert [message["role"] for message in saved_messages(sam_id, session_id)] == ["user"]


def test_a_reply_for_a_chat_that_does_not_exist_is_refused(app, held_replies):
    endpoint = routes.endpoints.add("https://models.example.com/v1", kind="api-key")
    held_replies.set()
    with TestClient(app, base_url="https://testserver", headers=HEADERS) as sam:
        signed_in(sam, "sam@example.com")
        refused = sam.post("/api/chat_stream", json={
            "endpoint_id": endpoint.id, "model": MODEL, "messages": [{"role": "user", "content": "hi"}],
            "session_id": "no-such-chat", "reply_index": 1})
    assert refused.status_code == 404


def test_the_server_does_not_add_a_reply_the_page_already_saved(app, held_replies):
    endpoint = routes.endpoints.add("https://models.example.com/v1", kind="api-key")
    held_replies.set()
    with TestClient(app, base_url="https://testserver", headers=HEADERS) as sam:
        signed_in(sam, "sam@example.com")
        session_id, question = chat_with_question(sam)
        page_copy = question + [{"role": "assistant", "content": "page copy"}]
        sam.put(f"/api/chat/sessions/{session_id}", json={"messages": page_copy})
        sam.post("/api/chat_stream", json={"endpoint_id": endpoint.id, "model": MODEL, "messages": question,
                                           "session_id": session_id, "reply_index": 1})
        saved = sam.get(f"/api/chat/sessions/{session_id}").json()["messages"]
    assert saved == page_copy


def test_a_reply_outside_a_saved_chat_streams_as_before(app, held_replies):
    endpoint = routes.endpoints.add("https://models.example.com/v1", kind="api-key")
    held_replies.set()
    with TestClient(app, base_url="https://testserver", headers=HEADERS) as sam:
        signed_in(sam, "sam@example.com")
        answer = sam.post("/api/chat_stream", json={"endpoint_id": endpoint.id, "model": MODEL,
                                                    "messages": [{"role": "user", "content": "hi"}]})
    assert [e for e in events(answer.text) if "reply" in e] == []
    assert "".join(e.get("delta", "") for e in events(answer.text)) == "first part, second part"


@pytest.mark.parametrize("closing", ["disable", "delete"])
def test_a_reply_in_a_chat_without_an_id_stops_when_its_account_is_disabled_or_removed(app, monkeypatch, closing):
    monkeypatch.setattr(routes, "ACCOUNT_RECHECK_SECONDS", 0, raising=False)
    started, release = threading.Event(), threading.Event()

    async def fake_stream(base_url, api_key, model, messages, system, options):
        yield line({"delta": "first part"})
        started.set()
        while not release.is_set():
            await asyncio.sleep(POLL_SECONDS)
        yield line({"delta": ", second part"})
        yield line({"done": True})

    monkeypatch.setattr(routes, "stream_chat", fake_stream)
    endpoint = routes.endpoints.add("https://models.example.com/v1", kind="api-key")
    sam_id = auth_routes.users.find_by_email("sam@example.com").id
    auth_routes.users.upsert("boss@example.com", "Boss", "admin", auth_routes.users.find_by_id(sam_id).password_hash)
    with TestClient(app, base_url="https://testserver", headers=HEADERS) as sam:
        signed_in(sam, "sam@example.com")
        answer = {}

        def ask():
            answer["response"] = sam.post("/api/chat_stream", json={
                "endpoint_id": endpoint.id, "model": MODEL, "messages": [{"role": "user", "content": "hi"}]})

        asking = threading.Thread(target=ask)
        asking.start()
        try:
            assert started.wait(WAIT_SECONDS)
            if closing == "disable":
                auth_routes.users.update(sam_id, disabled=True)
            else:
                auth_routes.users.delete(sam_id)
        finally:
            release.set()
            asking.join(WAIT_SECONDS)

    streamed = events(answer["response"].text)
    assert ", second part" not in [event.get("delta") for event in streamed]
    assert routes.CLOSED_ACCOUNT_STOPPED_REPLY in [event.get("error") for event in streamed]
