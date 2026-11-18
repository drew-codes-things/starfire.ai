import os
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
from config import config
from model_access import ModelAccessStore
from model_endpoints import ModelEndpointStore

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}
INTERNAL = "http://10.20.30.40:11434"


@pytest.fixture
def app(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    users, sessions = UserStore(data_dir), SessionStore(data_dir)
    monkeypatch.setattr(config, "data_dir", data_dir)
    monkeypatch.setattr(config, "ollama_base_url", INTERNAL)
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(auth_routes, "known_addresses", auth.KnownAddresses(data_dir))
    monkeypatch.setattr(routes, "registry", user_data.configure(data_dir))
    monkeypatch.setattr(routes, "api_keys", APIKeyManager(data_dir))
    monkeypatch.setattr(routes, "endpoints", ModelEndpointStore(data_dir))
    monkeypatch.setattr(routes, "model_access_store", ModelAccessStore(data_dir))
    routes.endpoints.add(INTERNAL + "/v1", kind="api-key", label="")
    hashed = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    users.upsert("admin@example.com", "Admin", "admin", hashed)
    users.upsert("sam@example.com", "Sam", "user", hashed)
    application = FastAPI()
    auth.install(application, users, sessions)
    application.include_router(auth_routes.router)
    application.include_router(routes.router)
    return application


def login(app, email):
    client = TestClient(app, base_url="https://testserver", headers=HEADERS)
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    return client


ADMIN_PAGES = ["/api/model-endpoints", "/api/comfyui/config", "/api/piper/config", "/api/hardware/local-server"]


def test_an_ordinary_account_gets_no_internal_address_from_config(app):
    config_answer = login(app, "sam@example.com").get("/api/config")
    assert config_answer.status_code == 200
    assert "10.20.30.40" not in config_answer.text and "ollama_base_url" not in config_answer.json()
    assert "own_tools_models" not in config_answer.json()


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_settings_that_hold_server_addresses_are_for_admins_only(app, path):
    assert login(app, "sam@example.com").get(path).status_code == 403
    assert login(app, "admin@example.com").get(path).status_code == 200


def test_the_admin_settings_field_still_gets_the_local_server_address(app):
    answer = login(app, "admin@example.com").get("/api/hardware/local-server").json()
    assert answer == {"ollama_base_url": INTERNAL}


def test_each_listed_model_says_whether_it_brings_its_own_tools(app, monkeypatch):
    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        reply = httpx.Response(200, json={"data": [{"id": "Arty"}, {"id": "arty/voice"}, {"id": "gpt-4o"}]})
        return real_client(*args, transport=httpx.MockTransport(lambda request: reply), **kwargs)

    monkeypatch.setattr(routes.httpx, "AsyncClient", client)
    listed = login(app, "sam@example.com").get("/api/models").json()["models"]
    assert {m["id"]: m["own_tools"] for m in listed} == {"Arty": True, "arty/voice": True, "gpt-4o": False}
    assert all("base_url" not in m for m in listed)
