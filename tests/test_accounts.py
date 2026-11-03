import os
import sys

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import auth
import auth_routes
import routes
import user_data
from auth import SessionStore, UserStore
from chat_session_store import ChatSessionStore

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}


def fast_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()


@pytest.fixture
def app(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    users = UserStore(data_dir)
    sessions = SessionStore(data_dir)
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(routes, "registry", user_data.configure(data_dir))
    monkeypatch.setattr(auth, "hash_password", fast_hash)
    application = FastAPI()
    auth.install(application, users, sessions)
    application.include_router(auth_routes.router)
    application.include_router(routes.router)
    return application


def login(app, email, password=PASSWORD):
    client = TestClient(app, base_url="https://testserver", headers=HEADERS)
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def admin(app):
    auth_routes.users.upsert("admin@example.com", "Admin", "admin", fast_hash(PASSWORD))
    return login(app, "admin@example.com")


def test_admin_creates_accounts_and_each_sees_only_its_own_chats(app, admin):
    created = admin.post("/api/auth/users", json={"email": "Sam@Example.com", "name": "Sam", "password": PASSWORD}).json()
    assert created["email"] == "sam@example.com" and created["role"] == "user"
    assert admin.post("/api/auth/users", json={"email": "sam@example.com", "password": PASSWORD}).status_code == 400
    assert admin.post("/api/auth/users", json={"email": "short@example.com", "password": "short"}).status_code == 400

    admin.post("/api/chat/sessions", json={"title": "Admin's chat"})
    sam = login(app, "sam@example.com")
    sam.post("/api/chat/sessions", json={"title": "Sam's chat"})
    assert [s["title"] for s in sam.get("/api/chat/sessions").json()["sessions"]] == ["Sam's chat"]
    assert [s["title"] for s in admin.get("/api/chat/sessions").json()["sessions"]] == ["Admin's chat"]
    assert len(sam.get("/api/note-templates").json()["templates"]) == 5

    users_dir = os.path.join(auth_routes.users.path.rsplit(os.sep, 1)[0], "users")
    assert sorted(os.listdir(users_dir)) == sorted(u["id"] for u in admin.get("/api/auth/users").json()["users"])


def test_users_cannot_manage_accounts_or_shared_settings(app, admin):
    admin.post("/api/auth/users", json={"email": "sam@example.com", "password": PASSWORD})
    sam = login(app, "sam@example.com")
    assert sam.get("/api/auth/users").status_code == 403
    assert sam.post("/api/model-endpoints", json={"base_url": "http://x", "kind": "openai"}).status_code == 403
    assert sam.post("/api/mcp/servers", json={"command": "npx"}).status_code == 403
    assert sam.get("/api/backup").status_code == 403
    assert sam.get("/api/model-endpoints").status_code == 200
    assert sam.get("/api/mcp/servers").status_code == 200
    assert sam.post("/api/notes", json={"title": "mine"}).status_code == 200


def test_admin_can_reset_disable_and_delete_an_account(app, admin):
    sam_id = admin.post("/api/auth/users", json={"email": "sam@example.com", "password": PASSWORD}).json()["id"]
    sam = login(app, "sam@example.com")
    sam.post("/api/chat/sessions", json={"title": "Sam's chat"})

    assert admin.post(f"/api/auth/users/{sam_id}/password", json={"password": "a brand new password"}).json() == {"ok": True}
    assert sam.get("/api/chat/sessions").status_code == 401
    sam = login(app, "sam@example.com", "a brand new password")

    assert admin.patch(f"/api/auth/users/{sam_id}", json={"disabled": True}).json()["disabled"] is True
    assert sam.get("/api/chat/sessions").status_code == 401
    bad = TestClient(app, base_url="https://testserver", headers=HEADERS)
    assert bad.post("/api/auth/login", json={"email": "sam@example.com", "password": "a brand new password"}).status_code == 401
    assert admin.patch(f"/api/auth/users/{sam_id}", json={"disabled": False, "role": "admin"}).json()["role"] == "admin"

    user_dir = routes.registry.dir_for(sam_id)
    assert ChatSessionStore(user_dir).list()
    assert admin.delete(f"/api/auth/users/{sam_id}").json() == {"ok": True}
    assert not os.path.exists(user_dir)
    assert admin.delete(f"/api/auth/users/{sam_id}").status_code == 404


def test_admin_cannot_remove_the_last_admin(app, admin):
    me = admin.get("/api/auth/me").json()
    assert admin.patch(f"/api/auth/users/{me['id']}", json={"role": "user"}).status_code == 400
    assert admin.delete(f"/api/auth/users/{me['id']}").status_code == 400
    other = admin.post("/api/auth/users", json={"email": "two@example.com", "password": PASSWORD, "role": "admin"}).json()
    assert admin.patch(f"/api/auth/users/{other['id']}", json={"role": "user"}).status_code == 200
    assert admin.patch(f"/api/auth/users/{other['id']}", json={"role": "user", "name": "Renamed"}).json()["name"] == "Renamed"


def test_legacy_single_user_data_moves_to_the_first_admin(tmp_path):
    registry = user_data.configure(str(tmp_path))
    ChatSessionStore(str(tmp_path)).add(title="Old chat")
    os.makedirs(os.path.join(str(tmp_path), "generated"))
    user_id = "a" * 32
    moved = registry.migrate_legacy(user_id)
    assert set(moved) == {"chat_sessions.json", "generated"}
    assert not os.path.exists(os.path.join(str(tmp_path), "chat_sessions.json"))
    with user_data.run_as(user_id):
        assert [s.title for s in user_data.PerUser("chat_sessions").list()] == ["Old chat"]
    assert registry.migrate_legacy(user_id) == []


def test_per_user_proxy_requires_a_signed_in_user(tmp_path):
    user_data.configure(str(tmp_path))
    with pytest.raises(user_data.NoUserContext):
        user_data.PerUser("notes").list()
