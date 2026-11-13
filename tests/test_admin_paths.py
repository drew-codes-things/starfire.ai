import io
import os
import sys
import zipfile

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
from api_key_manager import APIKeyManager
from auth import SessionStore, UserStore
from config import config
from local_gen_store import ComfyUIConfigStore, CustomWorkflowStore, PiperConfigStore
from mcp_servers_store import McpServerStore
from model_endpoints import ModelEndpointStore

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}


def fast_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()


@pytest.fixture
def app(tmp_path, monkeypatch):
    data_dir = str(tmp_path / "data")
    os.makedirs(data_dir)
    users, sessions = UserStore(data_dir), SessionStore(data_dir)
    monkeypatch.setattr(config, "data_dir", data_dir)
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(auth_routes, "known_addresses", auth.KnownAddresses(data_dir))
    monkeypatch.setattr(auth_routes, "account_limiter", auth.LoginLimiter())
    monkeypatch.setattr(routes, "registry", user_data.configure(data_dir))
    monkeypatch.setattr(routes, "api_keys", APIKeyManager(data_dir))
    monkeypatch.setattr(routes, "endpoints", ModelEndpointStore(data_dir))
    monkeypatch.setattr(routes, "mcp_servers", McpServerStore(data_dir))
    monkeypatch.setattr(routes, "comfyui_config", ComfyUIConfigStore(data_dir))
    monkeypatch.setattr(routes, "piper_config", PiperConfigStore(data_dir))
    monkeypatch.setattr(routes, "custom_workflows", CustomWorkflowStore(data_dir))
    monkeypatch.setattr(auth, "hash_password", fast_hash)
    users.upsert("admin@example.com", "Admin", "admin", fast_hash(PASSWORD))
    users.upsert("sam@example.com", "Sam", "user", fast_hash(PASSWORD))
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
    return login(app, "admin@example.com")


@pytest.fixture
def sam(app):
    return login(app, "sam@example.com")


def sam_id():
    return auth_routes.users.find_by_email("sam@example.com").id


def zip_of(files: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


ADMIN_ONLY_REQUESTS = [
    ("get", "/api/backup", None),
    ("get", "/api/browse-dir?path=/", None),
    ("get", "/api/discover", None),
    ("get", "/api/hardware", None),
    ("post", "/api/hardware/ollama/start", {}),
    ("post", "/api/hardware/pull", {"model": "llama3"}),
    ("post", "/api/model-endpoints", {"base_url": "http://10.0.0.1:11434"}),
    ("post", "/api/model-endpoints/test", {"base_url": "http://169.254.169.254/latest"}),
    ("delete", "/api/model-endpoints/ENDPOINT", None),
    ("post", "/api/mcp/servers", {"command": "sh", "args": ["-c", "id"]}),
    ("patch", "/api/mcp/servers/SERVER", {"enabled": False}),
    ("delete", "/api/mcp/servers/SERVER", None),
    ("put", "/api/comfyui/config", {"checkpoints_dir": "/etc"}),
    ("post", "/api/comfyui/pull-checkpoint", {"url": "http://x", "filename": "a"}),
    ("put", "/api/piper/config", {"voices_dir": "/etc"}),
    ("post", "/api/piper/pull-voice", {"voice_name": "en_US-lessac"}),
    ("post", "/api/custom-workflows", {"name": "w", "workflow": {"1": {}}, "prompt_node_id": "1"}),
    ("delete", "/api/custom-workflows/WORKFLOW", None),
    ("get", "/api/auth/users", None),
    ("post", "/api/auth/users", {"email": "new@example.com", "password": PASSWORD}),
    ("patch", "/api/auth/users/SAM", {"role": "admin"}),
    ("post", "/api/auth/users/SAM/password", {"password": "a different password"}),
    ("delete", "/api/auth/users/ADMIN", None),
    ("get", "/api/%62ackup", None),
    ("get", "/api/auth/users/", None),
]


@pytest.fixture
def shared_settings(admin):
    endpoint = routes.endpoints.add("http://127.0.0.1:11434", kind="ollama", label="Local")
    server = routes.mcp_servers.add(name="files", command="npx", args=["server-filesystem", "/srv"])
    workflow = routes.custom_workflows.add(name="clip", workflow={"1": {}}, prompt_node_id="1")
    routes.comfyui_config.update(checkpoints_dir="/models")
    routes.piper_config.update(voices_dir="/voices")
    return {"ENDPOINT": endpoint.id, "SERVER": server.id, "WORKFLOW": workflow.id,
            "SAM": sam_id(), "ADMIN": auth_routes.users.find_by_email("admin@example.com").id}


def snapshot():
    return {
        "endpoints": [e.id for e in routes.endpoints.list()],
        "servers": [(s.id, s.enabled, s.command, s.args) for s in routes.mcp_servers.list()],
        "workflows": [w.id for w in routes.custom_workflows.list()],
        "comfyui": routes.comfyui_config.get().checkpoints_dir,
        "piper": routes.piper_config.get().voices_dir,
        "users": [(u.email, u.role, u.disabled, u.password_hash) for u in auth_routes.users.list()],
    }


@pytest.mark.parametrize("method, path, body", ADMIN_ONLY_REQUESTS)
def test_an_ordinary_account_is_refused_every_admin_route_and_nothing_changes(shared_settings, sam, method, path, body):
    before = snapshot()
    for placeholder, value in shared_settings.items():
        path = path.replace(placeholder, value)
    response = getattr(sam, method)(path, json=body) if body is not None else getattr(sam, method)(path)
    assert response.status_code == 403, (path, response.status_code, response.text)
    assert snapshot() == before


def test_an_ordinary_account_cannot_restore_a_backup_over_the_server(admin, sam):
    archive = zip_of({"users.json": "[]"})
    before = open(auth_routes.users.path, encoding="utf-8").read()
    response = sam.post("/api/restore", files={"file": ("backup.zip", archive, "application/zip")})
    assert response.status_code == 403
    assert open(auth_routes.users.path, encoding="utf-8").read() == before


def test_a_restore_that_is_not_a_zip_is_refused(admin):
    response = admin.post("/api/restore", files={"file": ("backup.zip", b"this is not a zip", "application/zip")})
    assert response.status_code == 400 and "not a valid zip" in response.json()["detail"]


@pytest.mark.parametrize("member", ["../escaped.txt", "users/../../escaped.txt", "/tmp/starfire-escaped.txt",
                                    "../data-sibling/escaped.txt"])
def test_a_restore_with_a_path_outside_the_data_folder_is_refused_and_writes_nothing(admin, tmp_path, member):
    archive = zip_of({"inside.txt": b"would be fine", member: b"escaped"})
    response = admin.post("/api/restore", files={"file": ("backup.zip", archive, "application/zip")})
    assert response.status_code == 400 and "unsafe path" in response.json()["detail"]
    data_dir = tmp_path / "data"
    assert not (data_dir / "inside.txt").exists()
    assert not (tmp_path / "escaped.txt").exists()
    assert not (tmp_path / "data-sibling").exists()
    assert not os.path.exists("/tmp/starfire-escaped.txt")


def test_a_backup_then_restore_brings_back_an_accounts_data(admin):
    admin.post("/api/notes", json={"title": "Keep me"})
    archive = admin.get("/api/backup").content
    note_id = admin.get("/api/notes").json()["notes"][0]["id"]
    admin.delete(f"/api/notes/{note_id}")
    assert admin.get("/api/notes").json()["notes"] == []
    assert admin.post("/api/restore", files={"file": ("backup.zip", archive, "application/zip")}).json() == {"ok": True}
    routes.registry.forget(auth_routes.users.find_by_email("admin@example.com").id)
    assert [n["title"] for n in admin.get("/api/notes").json()["notes"]] == ["Keep me"]


def test_a_demoted_admin_loses_admin_routes_on_their_next_request(app, admin):
    other = admin.post("/api/auth/users", json={"email": "two@example.com", "password": PASSWORD,
                                                "role": "admin"}).json()
    second = login(app, "two@example.com")
    assert second.get("/api/auth/users").status_code == 200
    assert admin.patch(f"/api/auth/users/{other['id']}", json={"role": "user"}).status_code == 200
    assert second.get("/api/auth/users").status_code == 403
    assert second.get("/api/backup").status_code == 403


def test_a_deleted_account_is_signed_out_and_its_data_is_gone(admin, sam):
    sam.post("/api/notes", json={"title": "Sam's note"})
    user_dir = routes.registry.dir_for(sam_id())
    assert os.path.isdir(user_dir)
    assert admin.delete(f"/api/auth/users/{sam_id()}").json() == {"ok": True}
    assert sam.get("/api/notes").status_code == 401
    assert not os.path.exists(user_dir)


def test_an_admin_cannot_demote_disable_or_delete_their_own_account(admin):
    me = admin.get("/api/auth/me").json()["id"]
    assert admin.patch(f"/api/auth/users/{me}", json={"role": "user"}).status_code == 400
    assert admin.patch(f"/api/auth/users/{me}", json={"disabled": True}).status_code == 400
    assert admin.delete(f"/api/auth/users/{me}").status_code == 400
    assert admin.get("/api/auth/users").status_code == 200


@pytest.mark.parametrize("body, status", [
    ({"role": "owner"}, 400),
    ({"name": "x" * 500}, 400),
])
def test_account_changes_with_bad_values_are_refused(admin, sam, body, status):
    assert admin.patch(f"/api/auth/users/{sam_id()}", json=body).status_code == status
    assert auth_routes.users.find_by_email("sam@example.com").role == "user"


@pytest.mark.parametrize("method, path, body", [
    ("patch", "/api/auth/users/{missing}", {"name": "Ghost"}),
    ("post", "/api/auth/users/{missing}/password", {"password": "a long enough password"}),
    ("delete", "/api/auth/users/{missing}", None),
])
def test_changing_an_account_that_does_not_exist_says_so(admin, method, path, body):
    url = path.format(missing="f" * 32)
    response = getattr(admin, method)(url, json=body) if body is not None else getattr(admin, method)(url)
    assert response.status_code == 404


@pytest.mark.parametrize("body", [
    {"email": "not-an-email", "password": PASSWORD},
    {"email": "new@example.com", "password": "short"},
    {"email": "new@example.com", "password": PASSWORD, "role": "superuser"},
    {"email": "new@example.com", "password": "p" * 2000},
])
def test_an_admin_cannot_create_a_broken_account(admin, body):
    assert admin.post("/api/auth/users", json=body).status_code == 400
    assert auth_routes.users.find_by_email("new@example.com") is None


def test_a_reset_password_must_be_a_real_password(admin, sam):
    assert admin.post(f"/api/auth/users/{sam_id()}/password", json={"password": "short"}).status_code == 400
    assert admin.post(f"/api/auth/users/{sam_id()}/password", json={"password": "p" * 2000}).status_code == 400
    assert sam.get("/api/notes").status_code == 200


def test_changing_your_own_password_needs_the_current_one(app, sam):
    wrong = sam.post("/api/auth/password", json={"current": "not it", "password": "a fresh new password",
                                                 "confirm": "a fresh new password"})
    assert wrong.status_code == 400
    login(app, "sam@example.com")


def test_signed_out_requests_cannot_change_a_password_or_ask_who_they_are(app):
    stranger = TestClient(app, base_url="https://testserver", headers=HEADERS)
    assert stranger.get("/api/auth/me").status_code == 401
    assert stranger.post("/api/auth/password", json={"current": PASSWORD, "password": "a fresh new password",
                                                     "confirm": "a fresh new password"}).status_code == 401
    assert stranger.post("/api/auth/logout").status_code == 401


def test_a_disabled_account_cannot_sign_in_again_and_its_session_ends(app, admin, sam):
    assert admin.patch(f"/api/auth/users/{sam_id()}", json={"disabled": True}).json()["disabled"] is True
    assert sam.get("/api/notes").status_code == 401
    fresh = TestClient(app, base_url="https://testserver", headers=HEADERS)
    assert fresh.post("/api/auth/login", json={"email": "sam@example.com", "password": PASSWORD}).status_code == 401


def test_an_admin_browsing_folders_sees_only_visible_folders_and_a_way_up(admin, tmp_path):
    (tmp_path / "projects").mkdir()
    (tmp_path / ".secrets").mkdir()
    (tmp_path / "notes.txt").write_text("not a folder")
    listing = admin.get("/api/browse-dir", params={"path": str(tmp_path)}).json()
    assert [entry["name"] for entry in listing["entries"]] == ["data", "projects"]
    assert listing["path"] == str(tmp_path) and listing["parent"] == str(tmp_path.parent)
    assert admin.get("/api/browse-dir", params={"path": "/"}).json()["parent"] is None


def test_browsing_something_that_is_not_a_folder_is_refused(admin, tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    assert admin.get("/api/browse-dir", params={"path": str(tmp_path / "notes.txt")}).status_code == 400
    assert admin.get("/api/browse-dir", params={"path": str(tmp_path / "missing")}).status_code == 400


@pytest.fixture
def mcp_connections(monkeypatch):
    connected = []
    disconnected = []
    failing = set()

    async def connect(server):
        if server.command in failing:
            raise routes.McpConnectionError(f"{server.command} would not start")
        connected.append(server)
        return [{"name": "tool"}]

    async def disconnect(server_id):
        disconnected.append(server_id)

    monkeypatch.setattr(routes.mcp_manager, "connect_server", connect)
    monkeypatch.setattr(routes.mcp_manager, "disconnect", disconnect)
    return connected, disconnected, failing


@pytest.mark.parametrize("body, message", [
    ({"preset": "no-such-preset"}, "unknown preset"),
    ({"preset": "filesystem"}, "needs a directory path"),
    ({"preset": "github"}, "GITHUB_PERSONAL_ACCESS_TOKEN"),
    ({"transport": "stdio"}, "command is required"),
])
def test_an_mcp_server_with_missing_details_is_refused_and_not_started(admin, mcp_connections, body, message):
    response = admin.post("/api/mcp/servers", json=body)
    assert response.status_code == 400 and message in response.json()["detail"]
    assert mcp_connections[0] == [] and routes.mcp_servers.list() == []


def test_a_preset_mcp_server_gets_the_folder_it_was_given(admin, mcp_connections):
    added = admin.post("/api/mcp/servers", json={"preset": "filesystem", "path": "/srv/shared"}).json()
    assert added["args"][-1] == "/srv/shared" and added["tool_count"] == 1
    [server] = routes.mcp_servers.list()
    assert server.transport == "stdio" and server.url == "" and server.headers == {}


def test_an_mcp_server_that_will_not_start_is_not_kept(admin, mcp_connections):
    _, _, failing = mcp_connections
    failing.add("broken-server")
    response = admin.post("/api/mcp/servers", json={"command": "broken-server"})
    assert response.status_code == 502 and "would not start" in response.json()["detail"]
    assert routes.mcp_servers.list() == []


def test_an_mcp_server_can_be_switched_off_and_removed(admin, mcp_connections):
    _, disconnected, _ = mcp_connections
    server_id = admin.post("/api/mcp/servers", json={"command": "npx", "args": ["server"]}).json()["id"]
    assert admin.patch(f"/api/mcp/servers/{server_id}", json={}).status_code == 400
    assert admin.patch(f"/api/mcp/servers/{server_id}", json={"enabled": False}).json() == {"ok": True}
    assert routes.mcp_servers.get(server_id).enabled is False
    assert admin.delete(f"/api/mcp/servers/{server_id}").json() == {"ok": True}
    assert disconnected == [server_id]
    assert admin.patch(f"/api/mcp/servers/{server_id}", json={"enabled": True}).status_code == 404
    assert admin.delete(f"/api/mcp/servers/{server_id}").status_code == 404
