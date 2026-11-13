import logging
import os
import stat
import sys

import bcrypt
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import auth
import auth_routes
import routes
import user_data
from api_key_manager import APIKeyManager
from auth import SessionStore, UserStore
from model_endpoints import ModelEndpointStore

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}
SECRET = "sk-PLACEHOLDER-not-a-real-key"


def read_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def mode_of(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_a_saved_key_is_encrypted_on_disk_and_the_key_file_is_private(tmp_path):
    keys = APIKeyManager(str(tmp_path))
    keys.save("endpoint-1", SECRET)
    assert SECRET not in read_text(tmp_path / "api_keys.json")
    assert mode_of(tmp_path / ".key") == 0o600
    assert APIKeyManager(str(tmp_path)).load() == {"endpoint-1": SECRET}


def test_a_key_file_left_readable_by_others_is_tightened_when_used(tmp_path):
    keys = APIKeyManager(str(tmp_path))
    keys.save("endpoint-1", SECRET)
    os.chmod(tmp_path / ".key", 0o644)
    assert keys.load() == {"endpoint-1": SECRET}
    assert mode_of(tmp_path / ".key") == 0o600


def test_keys_from_another_install_are_skipped_not_a_crash(tmp_path, caplog):
    (tmp_path / "old").mkdir()
    (tmp_path / "new").mkdir()
    APIKeyManager(str(tmp_path / "old")).save("endpoint-1", SECRET)
    (tmp_path / "new" / "api_keys.json").write_text(read_text(tmp_path / "old" / "api_keys.json"))
    with caplog.at_level(logging.WARNING, logger="api_key_manager"):
        assert APIKeyManager(str(tmp_path / "new")).load() == {}
    assert "endpoint-1" in caplog.text and SECRET not in caplog.text


def test_deleting_one_key_keeps_the_others(tmp_path):
    keys = APIKeyManager(str(tmp_path))
    keys.save("a", "key-a")
    keys.save("b", "key-b")
    keys.delete("a")
    keys.delete("never-saved")
    assert keys.load() == {"b": "key-b"}


@pytest.fixture
def admin(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    users, sessions = UserStore(data_dir), SessionStore(data_dir)
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(auth_routes, "known_addresses", auth.KnownAddresses(data_dir))
    monkeypatch.setattr(routes, "registry", user_data.configure(data_dir))
    monkeypatch.setattr(routes, "api_keys", APIKeyManager(data_dir))
    monkeypatch.setattr(routes, "endpoints", ModelEndpointStore(data_dir))
    users.upsert("admin@example.com", "Admin", "admin", bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode())
    app = FastAPI()
    auth.install(app, users, sessions)
    app.include_router(auth_routes.router)
    app.include_router(routes.router)
    client = TestClient(app, base_url="https://testserver", headers=HEADERS)
    assert client.post("/api/auth/login", json={"email": "admin@example.com", "password": PASSWORD}).status_code == 200
    return client


@pytest.fixture
def model_servers(monkeypatch):
    seen = []
    replies = {}

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.headers.get("authorization"), request.headers.get("x-api-key")))
        status, body = replies.get(request.url.host, (200, {"data": []}))
        if status is None:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(status, json=body)

    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(routes.httpx, "AsyncClient", client)
    return seen, replies


def test_an_endpoint_key_is_stored_encrypted_and_never_listed(admin, model_servers):
    added = admin.post("/api/model-endpoints", json={"base_url": "https://models.example.com/v1", "api_key": SECRET})
    assert added.status_code == 200 and SECRET not in added.text
    assert SECRET not in admin.get("/api/model-endpoints").text
    assert SECRET not in read_text(routes.api_keys.api_keys_file)
    assert routes.api_keys.load() == {added.json()["id"]: SECRET}


def test_an_endpoint_that_refuses_the_key_is_not_added(admin, model_servers):
    _, replies = model_servers
    replies["models.example.com"] = (401, {"error": "bad key"})
    response = admin.post("/api/model-endpoints", json={"base_url": "https://models.example.com/v1", "api_key": SECRET})
    assert response.status_code == 502 and "authentication failed" in response.json()["detail"]
    assert admin.get("/api/model-endpoints").json()["endpoints"] == []
    assert routes.api_keys.load() == {}


def test_an_unreachable_endpoint_is_reported_and_not_added(admin, model_servers):
    _, replies = model_servers
    replies["down.example.com"] = (None, None)
    response = admin.post("/api/model-endpoints/test", json={"base_url": "https://down.example.com/v1"})
    assert response.json()["ok"] is False and "cannot reach" in response.json()["message"]
    assert admin.post("/api/model-endpoints", json={"base_url": "https://down.example.com/v1"}).status_code == 502
    assert admin.get("/api/model-endpoints").json()["endpoints"] == []


def test_the_same_endpoint_cannot_be_added_twice(admin, model_servers):
    assert admin.post("/api/model-endpoints", json={"base_url": "https://models.example.com/v1"}).status_code == 200
    assert admin.post("/api/model-endpoints", json={"base_url": "https://models.example.com/v1/"}).status_code == 409
    assert admin.post("/api/model-endpoints", json={"base_url": "   "}).status_code == 400


def test_removing_an_endpoint_also_removes_its_key(admin, model_servers):
    endpoint_id = admin.post("/api/model-endpoints", json={"base_url": "https://models.example.com/v1",
                                                           "api_key": SECRET}).json()["id"]
    assert admin.delete(f"/api/model-endpoints/{endpoint_id}").json() == {"ok": True}
    assert routes.api_keys.load() == {}
    assert admin.delete(f"/api/model-endpoints/{endpoint_id}").status_code == 404


def test_each_endpoint_only_ever_receives_its_own_key(admin, model_servers):
    seen, replies = model_servers
    admin.post("/api/model-endpoints", json={"base_url": "https://one.example.com/v1", "api_key": "key-for-one"})
    admin.post("/api/model-endpoints", json={"base_url": "https://two.example.com/v1"})
    admin.post("/api/model-endpoints", json={"base_url": "https://api.anthropic.com", "api_key": "key-for-claude"})
    replies["one.example.com"] = (200, {"data": [{"id": "one-model"}]})
    replies["two.example.com"] = (200, {"data": [{"id": "two-model"}]})
    replies["api.anthropic.com"] = (200, {"data": [{"id": "claude-model"}]})
    seen.clear()

    models = admin.get("/api/models").json()["models"]

    assert sorted(m["id"] for m in models) == ["claude-model", "one-model", "two-model"]
    by_host = {host: (bearer, x_api_key) for host, bearer, x_api_key in seen}
    assert by_host["one.example.com"] == ("Bearer key-for-one", None)
    assert by_host["two.example.com"] == (None, None)
    assert by_host["api.anthropic.com"] == (None, "key-for-claude")


def test_a_model_server_that_fails_is_skipped_and_embedding_models_are_hidden(admin, model_servers):
    seen, replies = model_servers
    admin.post("/api/model-endpoints", json={"base_url": "http://localhost:11434"})
    admin.post("/api/model-endpoints", json={"base_url": "https://broken.example.com/v1"})
    replies["localhost"] = (200, {"models": [{"name": "qwen3:14b", "size": 9}, {"name": "nomic-embed-text"}]})
    replies["broken.example.com"] = (500, {"error": "down"})
    models = admin.get("/api/models").json()["models"]
    assert [(m["id"], m["provider"]) for m in models] == [("qwen3:14b", "ollama")]
