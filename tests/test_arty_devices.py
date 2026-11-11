import json
import os
import sys

import bcrypt
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import arty_devices
import auth
from api_key_manager import APIKeyManager
from auth import SESSION_COOKIE, SessionStore, UserStore
from config import config
from model_endpoints import ModelEndpointStore

BRAIN = "http://brain.test:8081/v1"
ARTY_KEY = "starfire-arty-key"
PASSWORD = "correct horse battery staple"


class FakeBrain:
    def __init__(self):
        self.calls = []
        self.answers = {}
        self.down = False

    def answer(self, method, path, status, body):
        self.answers[(method, path)] = (status, body)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        body = json.loads(request.content) if request.content else None
        self.calls.append({"method": request.method, "url": str(request.url), "auth": request.headers.get("authorization"),
                           "body": body})
        status, answer = self.answers.get((request.method, request.url.path), (404, {"error": {"message": "not found"}}))
        return httpx.Response(status, json=answer)


@pytest.fixture
def brain(monkeypatch):
    fake = FakeBrain()
    monkeypatch.setattr(arty_devices, "brain_transport", httpx.MockTransport(fake.handle))
    return fake


@pytest.fixture
def setup(tmp_path, monkeypatch, brain):
    data_dir = str(tmp_path)
    endpoints = ModelEndpointStore(data_dir)
    keys = APIKeyManager(data_dir)
    endpoint = endpoints.add(BRAIN, kind="api-key", label="Arty")
    keys.save(endpoint.id, ARTY_KEY)
    monkeypatch.setattr(arty_devices, "endpoints", endpoints)
    monkeypatch.setattr(arty_devices, "api_keys", keys)
    monkeypatch.setattr(config, "arty_base_url", BRAIN)
    monkeypatch.setattr(config, "arty_phone_url", "https://arty.example")
    monkeypatch.setattr(config, "arty_access_client_id", "")
    monkeypatch.setattr(config, "arty_access_client_secret", "")
    users, sessions = UserStore(data_dir), SessionStore(data_dir)
    hashed = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    admin = users.upsert("admin@example.com", "Admin", "admin", hashed)
    member = users.upsert("member@example.com", "Member", "user", hashed)
    app = FastAPI()
    auth.install(app, users, sessions)
    app.include_router(arty_devices.router)
    return {"app": app, "sessions": sessions, "admin": admin, "member": member, "endpoints": endpoints, "keys": keys}


def client_for(setup, who):
    client = TestClient(setup["app"])
    if who:
        client.cookies.set(SESSION_COOKIE, setup["sessions"].create(setup[who].id))
    return client


@pytest.fixture
def admin(setup):
    return client_for(setup, "admin")


def test_only_an_admin_can_use_it_even_to_look(setup, brain):
    member = client_for(setup, "member")
    for method, path in (("GET", "/api/arty-devices"), ("GET", "/api/arty-devices/pending?code=ABCD-EFGH"),
                         ("GET", "/api/arty-devices/status"), ("POST", "/api/arty-devices/keys")):
        assert member.request(method, path, json={"name": "x"}).status_code == 403, path
    assert client_for(setup, None).get("/api/arty-devices").status_code == 401
    assert brain.calls == []


def test_status_says_what_is_missing(setup, admin, monkeypatch):
    assert admin.get("/api/arty-devices/status").json() == {"ready": True, "message": ""}
    monkeypatch.setattr(config, "arty_base_url", "")
    missing_address = admin.get("/api/arty-devices/status").json()
    assert missing_address["ready"] is False and "ARTY_BASE_URL" in missing_address["message"]
    monkeypatch.setattr(config, "arty_base_url", "http://elsewhere.test:8081/v1")
    missing_key = admin.get("/api/arty-devices/status").json()
    assert missing_key["ready"] is False and "Connections" in missing_key["message"]


def test_a_waiting_computer_is_shown_then_approved_with_starfires_arty_key(admin, brain):
    brain.answer("GET", "/v1/device/pending", 200, {"name": "Arty CLI on box", "address": "192.0.2.5", "user_code": "ABCD-EFGH",
                                                    "expires_in": 500})
    brain.answer("POST", "/v1/device/approve", 200, {"ok": True})
    waiting = admin.get("/api/arty-devices/pending", params={"code": "abcd efgh"})
    assert waiting.status_code == 200 and waiting.json()["name"] == "Arty CLI on box"
    assert admin.post("/api/arty-devices/approve", json={"code": "ABCDEFGH", "approve": True}).json() == {"ok": True}
    assert brain.calls[0]["url"] == f"{BRAIN}/device/pending?code=ABCD-EFGH"
    assert brain.calls[1]["body"] == {"user_code": "ABCD-EFGH", "approve": True}
    assert {call["auth"] for call in brain.calls} == {f"Bearer {ARTY_KEY}"}


def test_a_computer_can_be_turned_down(admin, brain):
    brain.answer("POST", "/v1/device/approve", 200, {"ok": True})
    assert admin.post("/api/arty-devices/approve", json={"code": "ABCD-EFGH", "approve": False}).status_code == 200
    assert brain.calls[0]["body"] == {"user_code": "ABCD-EFGH", "approve": False}


@pytest.mark.parametrize("code", ["abc", "ABCD-EFG1", "<script>", ""])
def test_a_bad_code_never_reaches_arty(admin, brain, code):
    assert admin.get("/api/arty-devices/pending", params={"code": code}).status_code == 400
    assert admin.post("/api/arty-devices/approve", json={"code": code, "approve": True}).status_code == 400
    assert brain.calls == []


def test_approve_must_be_a_real_yes_or_no(admin, brain):
    assert admin.post("/api/arty-devices/approve", json={"code": "ABCD-EFGH", "approve": "yes"}).status_code == 422
    assert brain.calls == []


def test_an_unknown_code_is_a_plain_not_found(admin, brain):
    response = admin.get("/api/arty-devices/pending", params={"code": "ABCD-EFGH"})
    assert response.status_code == 404 and "run out" in response.json()["detail"]


def test_devices_are_listed_and_turned_off(admin, brain):
    brain.answer("GET", "/v1/devices", 200, {"devices": [{"id": 3, "name": "laptop", "address": "192.0.2.5", "created": 1.0,
                                                          "last_used": None}]})
    brain.answer("DELETE", "/v1/devices/3", 200, {"ok": True})
    assert admin.get("/api/arty-devices").json()["devices"][0]["name"] == "laptop"
    assert admin.delete("/api/arty-devices/3").json() == {"ok": True}
    assert brain.calls[1]["method"] == "DELETE" and brain.calls[1]["url"] == f"{BRAIN}/devices/3"


def test_a_new_key_comes_back_once_with_the_phone_setup_code(admin, brain):
    brain.answer("POST", "/v1/devices", 201, {"id": 4, "name": "Sam's phone", "api_key": "arty_dev_made"})
    response = admin.post("/api/arty-devices/keys", json={"name": "Sam's phone"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    made = response.json()
    assert made["api_key"] == "arty_dev_made" and made["name"] == "Sam's phone"
    assert made["setup_code"] == '{"arty":1,"url":"https://arty.example","key":"arty_dev_made"}'
    assert brain.calls[0]["body"] == {"name": "Sam's phone"}


def test_the_setup_code_carries_the_cloudflare_token_only_when_starfire_has_both_parts(admin, brain, monkeypatch):
    brain.answer("POST", "/v1/devices", 201, {"id": 4, "name": "p", "api_key": "arty_dev_made"})
    monkeypatch.setattr(config, "arty_access_client_id", "cf-id")
    half = json.loads(admin.post("/api/arty-devices/keys", json={"name": "p"}).json()["setup_code"])
    assert "id" not in half and "secret" not in half
    monkeypatch.setattr(config, "arty_access_client_secret", "cf-secret")
    full = json.loads(admin.post("/api/arty-devices/keys", json={"name": "p"}).json()["setup_code"])
    assert (full["id"], full["secret"]) == ("cf-id", "cf-secret")


def test_without_a_phone_address_the_brain_address_is_used(admin, brain, monkeypatch):
    brain.answer("POST", "/v1/devices", 201, {"id": 4, "name": "p", "api_key": "arty_dev_made"})
    monkeypatch.setattr(config, "arty_phone_url", "")
    made = json.loads(admin.post("/api/arty-devices/keys", json={"name": "p"}).json()["setup_code"])
    assert made["url"] == "http://brain.test:8081"


def test_a_key_name_is_needed_and_kept_short(admin, brain):
    assert admin.post("/api/arty-devices/keys", json={"name": ""}).status_code == 422
    assert admin.post("/api/arty-devices/keys", json={"name": "x" * 81}).status_code == 422
    assert brain.calls == []


def test_when_arty_is_down_the_error_says_so(admin, brain):
    brain.down = True
    response = admin.get("/api/arty-devices")
    assert response.status_code == 502 and "isn't answering" in response.json()["detail"]


def test_arty_refusing_starfires_key_is_explained(admin, brain):
    brain.answer("GET", "/v1/devices", 401, {"error": {"message": "invalid API key"}})
    response = admin.get("/api/arty-devices")
    assert response.status_code == 502 and "Connections" in response.json()["detail"]


def test_arty_saying_too_many_keys_is_passed_on(admin, brain):
    brain.answer("POST", "/v1/devices", 429, {"error": {"message": "too many device keys are live"}})
    response = admin.post("/api/arty-devices/keys", json={"name": "p"})
    assert response.status_code == 429 and "too many" in response.json()["detail"]


def test_nothing_is_asked_of_arty_until_it_is_set_up(admin, brain, monkeypatch):
    monkeypatch.setattr(config, "arty_base_url", "")
    assert admin.get("/api/arty-devices").status_code == 503
    assert brain.calls == []
