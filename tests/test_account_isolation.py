import asyncio
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
import email_client
import routes
import user_data
from api_key_manager import APIKeyManager
from auth import SessionStore, UserStore
from config import config

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}
PNG = b"\x89PNG fake image bytes"
MAILBOX_CALLS = ("list_folders", "list_messages", "read_message", "search_messages", "send_message",
                 "reply_message", "mark_read", "archive_message", "delete_message")


def fast_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()


@pytest.fixture
def mailbox(monkeypatch):
    calls = []

    def recorder(name):
        def record(*args, **kwargs):
            calls.append(name)
            return [] if name.startswith(("list", "search")) else {"from": "", "subject": "", "body": ""}
        return record

    for name in MAILBOX_CALLS:
        monkeypatch.setattr(email_client, name, recorder(name))
    return calls


@pytest.fixture
def app(tmp_path, monkeypatch, mailbox):
    data_dir = str(tmp_path)
    users, sessions = UserStore(data_dir), SessionStore(data_dir)
    monkeypatch.setattr(config, "data_dir", data_dir)
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(auth_routes, "known_addresses", auth.KnownAddresses(data_dir))
    monkeypatch.setattr(auth_routes, "account_limiter", auth.LoginLimiter())
    monkeypatch.setattr(routes, "registry", user_data.configure(data_dir))
    monkeypatch.setattr(routes, "api_keys", APIKeyManager(data_dir))
    monkeypatch.setattr(auth, "hash_password", fast_hash)
    users.upsert("owner@example.com", "Owner", "admin", fast_hash(PASSWORD))
    users.upsert("other@example.com", "Other", "user", fast_hash(PASSWORD))
    application = FastAPI()
    auth.install(application, users, sessions)
    application.include_router(auth_routes.router)
    application.include_router(routes.router)
    return application


def login(app, email):
    client = TestClient(app, base_url="https://testserver", headers=HEADERS)
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    return client


@pytest.fixture
def owner(app):
    return login(app, "owner@example.com")


@pytest.fixture
def other(app):
    return login(app, "other@example.com")


@pytest.fixture
def owned(owner, mailbox):
    note = owner.post("/api/notes", json={"title": "Private plans", "note_type": "checklist",
                                          "items": [{"text": "secret step"}]}).json()
    memory = owner.post("/api/memory", json={"text": "my bank is in Leeds"}).json()
    task = owner.post("/api/tasks", json={"prompt": "summarise my inbox", "schedule": "daily",
                                          "scheduled_time": "08:00"}).json()
    document = owner.post("/api/documents", files={"file": ("plans.txt", b"the private plan text")}).json()
    attachment = owner.post("/api/attachments", files={"file": ("me.png", PNG, "image/png")}).json()
    preset = owner.post("/api/presets", json={"name": "Mine"}).json()
    prompt = owner.post("/api/prompts", json={"command": "mine", "content": "secret prompt"}).json()
    folder = owner.post("/api/folders", json={"name": "Private"}).json()
    session = owner.post("/api/chat/sessions", json={"title": "Private chat"}).json()
    account = owner.post("/api/email/accounts", json={"email_address": "owner@mail.example", "password": "mail-pass",
                                                      "imap_host": "imap.mail.example"}).json()
    rule = owner.post("/api/email/rules", json={"account_id": account["id"], "match_value": "boss"}).json()
    template = owner.post("/api/note-templates", json={"name": "Mine", "title": "From my template"}).json()
    me = owner.get("/api/auth/me").json()
    with user_data.run_as(me["id"]):
        generated = routes.generated_files.add("document", "report.txt", "text/plain", b"private report")
    mailbox.clear()
    return {"note": note["id"], "memory": memory["id"], "task": task["id"], "document": document["id"],
            "attachment": attachment["id"], "preset": preset["id"], "prompt": prompt["id"], "folder": folder["id"],
            "session": session["id"], "account": account["id"], "rule": rule["id"], "template": template["id"],
            "generated": generated.id}


FOREIGN_REQUESTS = [
    ("get", "/api/notes/{note}", None),
    ("put", "/api/notes/{note}", {"title": "taken"}),
    ("delete", "/api/notes/{note}", None),
    ("post", "/api/notes/{note}/pin", {"pinned": True}),
    ("post", "/api/notes/{note}/archive", {"archived": True}),
    ("post", "/api/notes/{note}/items/0/toggle", None),
    ("put", "/api/memory/{memory}", {"text": "changed"}),
    ("delete", "/api/memory/{memory}", None),
    ("post", "/api/memory/{memory}/pin", {"pinned": True}),
    ("post", "/api/tasks/{task}/pause", None),
    ("post", "/api/tasks/{task}/resume", None),
    ("post", "/api/tasks/{task}/run", None),
    ("delete", "/api/tasks/{task}", None),
    ("get", "/api/documents/{document}/text", None),
    ("delete", "/api/documents/{document}", None),
    ("get", "/api/attachments/{attachment}", None),
    ("delete", "/api/presets/{preset}", None),
    ("put", "/api/prompts/{prompt}", {"command": "taken", "content": "x"}),
    ("delete", "/api/prompts/{prompt}", None),
    ("put", "/api/folders/{folder}", {"name": "taken"}),
    ("delete", "/api/folders/{folder}", None),
    ("get", "/api/chat/sessions/{session}", None),
    ("put", "/api/chat/sessions/{session}", {"title": "taken"}),
    ("post", "/api/chat/sessions/{session}/clone", None),
    ("delete", "/api/chat/sessions/{session}", None),
    ("get", "/api/email/{account}/folders", None),
    ("get", "/api/email/{account}/messages", None),
    ("get", "/api/email/{account}/message/7", None),
    ("get", "/api/email/{account}/search?query=boss", None),
    ("post", "/api/email/{account}/send", {"to": "x@example.com", "body": "hi"}),
    ("post", "/api/email/{account}/message/7/reply", {"body": "hi"}),
    ("post", "/api/email/{account}/message/7/mark-read", None),
    ("post", "/api/email/{account}/message/7/archive", None),
    ("delete", "/api/email/{account}/message/7", None),
    ("delete", "/api/email/accounts/{account}", None),
    ("patch", "/api/email/rules/{rule}", {"enabled": False}),
    ("delete", "/api/email/rules/{rule}", None),
    ("delete", "/api/note-templates/{template}", None),
    ("post", "/api/note-templates/{template}/use", None),
    ("get", "/api/generated/{generated}", None),
    ("delete", "/api/generated/{generated}", None),
]


@pytest.mark.parametrize("method, path, body", FOREIGN_REQUESTS)
def test_another_account_cannot_reach_items_by_their_id(owned, other, mailbox, method, path, body):
    url = path.format(**owned)
    response = getattr(other, method)(url, json=body) if body is not None else getattr(other, method)(url)
    assert response.status_code == 404, (url, response.status_code, response.text)
    assert mailbox == []


def test_after_every_foreign_attempt_the_owner_still_has_everything_unchanged(owned, owner, other):
    for method, path, body in FOREIGN_REQUESTS:
        url = path.format(**owned)
        getattr(other, method)(url, json=body) if body is not None else getattr(other, method)(url)

    note = owner.get(f"/api/notes/{owned['note']}").json()
    assert note["title"] == "Private plans" and note["pinned"] is False and note["items"][0]["done"] is False
    assert [m["text"] for m in owner.get("/api/memory").json()["memories"]] == ["my bank is in Leeds"]
    assert owner.get("/api/tasks").json()["tasks"][0]["status"] == "active"
    assert owner.get(f"/api/tasks/{owned['task']}/runs").json()["runs"] == []
    assert owner.get(f"/api/documents/{owned['document']}/text").json()["text"] == "the private plan text"
    assert owner.get(f"/api/attachments/{owned['attachment']}").content == PNG
    assert [p["name"] for p in owner.get("/api/presets").json()["presets"]] == ["Mine"]
    assert [f["name"] for f in owner.get("/api/folders").json()["folders"]] == ["Private"]
    assert owner.get(f"/api/chat/sessions/{owned['session']}").json()["title"] == "Private chat"
    assert [a["id"] for a in owner.get("/api/email/accounts").json()["accounts"]] == [owned["account"]]
    assert owner.get("/api/email/rules").json()["rules"][0]["enabled"] is True
    assert owner.get(f"/api/generated/{owned['generated']}").content == b"private report"


def test_another_account_lists_none_of_the_owners_items(owned, other):
    listings = {
        "/api/notes": "notes", "/api/memory": "memories", "/api/tasks": "tasks", "/api/documents": "documents",
        "/api/presets": "presets", "/api/folders": "folders", "/api/chat/sessions": "sessions",
        "/api/email/accounts": "accounts", "/api/email/rules": "rules", "/api/generated": "files",
        "/api/tasks/runs/recent": "runs",
    }
    for path, key in listings.items():
        assert other.get(path).json()[key] == [], path
    assert other.get(f"/api/tasks/{owned['task']}/runs").json()["runs"] == []
    assert other.get(f"/api/chat/sessions/{owned['session']}/branches").json()["branches"] == []
    assert "From my template" not in other.get("/api/note-templates").text
    assert "secret prompt" not in other.get("/api/prompts").text


def test_an_email_rule_cannot_borrow_another_accounts_mailbox(owned, other, mailbox, monkeypatch):
    rule = other.post("/api/email/rules", json={"account_id": owned["account"], "match_value": "x",
                                                "action": "delete"}).json()
    assert rule["account_id"] == owned["account"]
    other_id = other.get("/api/auth/me").json()["id"]
    with user_data.run_as(other_id):
        asyncio.run(routes.email_rule_checker._tick_current_user())
    assert mailbox == []


def test_the_email_password_never_comes_back_from_the_api(owned, owner):
    for path in ("/api/email/accounts", "/api/email/rules", f"/api/email/{owned['account']}/folders"):
        assert "mail-pass" not in owner.get(path).text
    stored = open(routes.api_keys.api_keys_file, encoding="utf-8").read()
    assert "mail-pass" not in stored and owned["account"] in stored
    assert routes.api_keys.load()[owned["account"]] == "mail-pass"


def test_deleting_an_email_account_also_forgets_its_password(owned, owner):
    assert owner.delete(f"/api/email/accounts/{owned['account']}").json() == {"ok": True}
    assert owned["account"] not in routes.api_keys.load()
    assert owner.get(f"/api/email/{owned['account']}/folders").status_code == 404


def test_an_email_account_that_cannot_connect_is_not_kept_and_its_password_is_dropped(owner, monkeypatch):
    def refuse(account, password):
        raise OSError("connection refused")

    monkeypatch.setattr(email_client, "list_folders", refuse)
    response = owner.post("/api/email/accounts", json={"email_address": "x@mail.example", "password": "pw-123",
                                                       "imap_host": "imap.mail.example"})
    assert response.status_code == 502 and "connection refused" in response.json()["detail"]
    assert owner.get("/api/email/accounts").json()["accounts"] == []
    assert "pw-123" not in routes.api_keys.load().values()
