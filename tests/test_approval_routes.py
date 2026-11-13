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
import file_edit_tool
import routes
import shell_tool
import user_data
from auth import SessionStore, UserStore

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}


@pytest.fixture
def app(tmp_path, monkeypatch):
    data_dir = str(tmp_path / "data")
    os.makedirs(data_dir)
    users, sessions = UserStore(data_dir), SessionStore(data_dir)
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(auth_routes, "known_addresses", auth.KnownAddresses(data_dir))
    monkeypatch.setattr(routes, "registry", user_data.configure(data_dir))
    hashed = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    users.upsert("admin@example.com", "Admin", "admin", hashed)
    users.upsert("sam@example.com", "Sam", "user", hashed)
    application = FastAPI()
    auth.install(application, users, sessions)
    application.include_router(auth_routes.router)
    application.include_router(routes.router)
    yield application
    for edit in list(file_edit_tool._pending.values()):
        file_edit_tool.remove_pending(edit["id"], edit["owner"])


def login(app, email):
    client = TestClient(app, base_url="https://testserver", headers=HEADERS)
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    client.user_id = auth_routes.users.find_by_email(email).id
    return client


@pytest.fixture
def admin(app):
    return login(app, "admin@example.com")


@pytest.fixture
def sam(app):
    return login(app, "sam@example.com")


def stage_edit(owner, path, content="new content\n"):
    diff = file_edit_tool.make_diff(str(path), file_edit_tool.read_current(str(path)), content)
    return file_edit_tool.stage(str(path), content, diff, owner=owner.user_id)


def test_approving_an_edit_writes_it_once(admin, tmp_path):
    target = tmp_path / "notes" / "todo.txt"
    edit_id = stage_edit(admin, target)
    assert [e["id"] for e in admin.get("/api/pending-edits").json()["edits"]] == [edit_id]
    assert admin.post(f"/api/pending-edits/{edit_id}/approve").json() == {"ok": True, "path": str(target)}
    assert target.read_text() == "new content\n"
    assert admin.get("/api/pending-edits").json()["edits"] == []
    assert admin.post(f"/api/pending-edits/{edit_id}/approve").status_code == 404


def test_rejecting_an_edit_leaves_the_file_alone(admin, tmp_path):
    target = tmp_path / "keep.txt"
    target.write_text("original\n")
    edit_id = stage_edit(admin, target)
    assert admin.post(f"/api/pending-edits/{edit_id}/reject").json() == {"ok": True}
    assert target.read_text() == "original\n"
    assert admin.post(f"/api/pending-edits/{edit_id}/reject").status_code == 404
    assert admin.post(f"/api/pending-edits/{edit_id}/approve").status_code == 404


def test_another_account_cannot_see_approve_or_reject_an_edit(admin, sam, tmp_path):
    target = tmp_path / "server.conf"
    edit_id = stage_edit(admin, target)
    assert sam.get("/api/pending-edits").json()["edits"] == []
    assert sam.post(f"/api/pending-edits/{edit_id}/approve").status_code == 403
    assert sam.post(f"/api/pending-edits/{edit_id}/reject").status_code == 404
    assert not target.exists()
    assert [e["id"] for e in admin.get("/api/pending-edits").json()["edits"]] == [edit_id]


def test_an_edit_that_cannot_be_written_reports_why_and_is_dropped(admin, tmp_path):
    folder = tmp_path / "a-folder"
    folder.mkdir()
    edit_id = stage_edit(admin, folder)
    response = admin.post(f"/api/pending-edits/{edit_id}/approve")
    assert response.status_code == 500 and "a-folder" in response.json()["detail"]
    assert admin.get("/api/pending-edits").json()["edits"] == []


def test_another_account_cannot_approve_or_reject_a_shell_command(admin, sam):
    async def scenario():
        pending_id = shell_tool.stage("rm -rf /srv/data", None, owner=admin.user_id)
        assert sam.get("/api/pending-shell").json()["commands"] == []
        assert sam.post(f"/api/pending-shell/{pending_id}/approve").status_code == 403
        assert sam.post(f"/api/pending-shell/{pending_id}/reject").status_code == 404
        assert [c["id"] for c in admin.get("/api/pending-shell").json()["commands"]] == [pending_id]
        assert admin.post(f"/api/pending-shell/{pending_id}/reject").json() == {"rejected": True}
        assert await shell_tool.wait_for_decision(pending_id) is False
        assert admin.post(f"/api/pending-shell/{pending_id}/reject").status_code == 404

    asyncio.run(scenario())


def test_a_command_can_only_be_decided_once(admin):
    async def scenario():
        pending_id = shell_tool.stage("echo once", None, owner=admin.user_id)
        assert admin.post(f"/api/pending-shell/{pending_id}/approve").json() == {"approved": True}
        assert admin.post(f"/api/pending-shell/{pending_id}/reject").status_code == 404
        assert await shell_tool.wait_for_decision(pending_id) is True

    asyncio.run(scenario())


def demote(client):
    auth_routes.users.update(client.user_id, role="user")


def test_an_admin_demoted_after_a_command_was_staged_cannot_approve_it(admin):
    other_admin = auth_routes.users.find_by_email("sam@example.com")
    auth_routes.users.update(other_admin.id, role="admin")

    async def scenario():
        pending_id = shell_tool.stage("echo staged-while-admin", None, owner=admin.user_id)
        demote(admin)
        response = admin.post(f"/api/pending-shell/{pending_id}/approve")
        assert response.status_code == 403
        assert response.json()["detail"].startswith("only an admin can")
        assert not shell_tool._pending[pending_id]["future"].done()
        assert [c["id"] for c in admin.get("/api/pending-shell").json()["commands"]] == [pending_id]
        assert admin.post(f"/api/pending-shell/{pending_id}/reject").json() == {"rejected": True}
        assert await shell_tool.wait_for_decision(pending_id) is False

    asyncio.run(scenario())


def test_an_admin_demoted_after_an_edit_was_staged_cannot_approve_it(admin, tmp_path):
    other_admin = auth_routes.users.find_by_email("sam@example.com")
    auth_routes.users.update(other_admin.id, role="admin")
    target = tmp_path / "staged-while-admin.txt"
    edit_id = stage_edit(admin, target)
    demote(admin)
    response = admin.post(f"/api/pending-edits/{edit_id}/approve")
    assert response.status_code == 403
    assert response.json()["detail"].startswith("only an admin can")
    assert not target.exists()
    assert [e["id"] for e in admin.get("/api/pending-edits").json()["edits"]] == [edit_id]


def test_a_disabled_admin_cannot_approve_what_was_staged_earlier(admin, tmp_path):
    other_admin = auth_routes.users.find_by_email("sam@example.com")
    auth_routes.users.update(other_admin.id, role="admin")
    target = tmp_path / "staged-before-disabling.txt"
    edit_id = stage_edit(admin, target)
    auth_routes.users.update(admin.user_id, disabled=True)
    assert admin.post(f"/api/pending-edits/{edit_id}/approve").status_code in (401, 403)
    assert not target.exists()
    assert file_edit_tool.get_pending(edit_id, admin.user_id) is not None
