import io
import json
import logging
import os
import sys
import zipfile

import bcrypt
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
from mcp_servers_store import McpServerStore
from model_endpoints import ModelEndpointStore

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}
PROVIDER_KEY = "sk-PLACEHOLDER-provider-key"
MAIL_PASSWORD = "PLACEHOLDER-mail-password"
MCP_TOKEN = "ghp_PLACEHOLDER-mcp-token"
MCP_BEARER = "Bearer PLACEHOLDER-mcp-bearer"
DOTENV_SECRET = "PLACEHOLDER-dotenv-secret"
FAKE_SECRETS = (PROVIDER_KEY, MAIL_PASSWORD, MCP_TOKEN, MCP_BEARER, DOTENV_SECRET)


def fast_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()


def install(data_dir: str, monkeypatch, admin_email: str) -> FastAPI:
    os.makedirs(data_dir, exist_ok=True)
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
    monkeypatch.setattr(auth, "hash_password", fast_hash)
    monkeypatch.setattr(routes.email_client, "list_folders", lambda account, password: ["INBOX"])
    users.upsert(admin_email, "Admin", "admin", fast_hash(PASSWORD))
    application = FastAPI()
    auth.install(application, users, sessions)
    application.include_router(auth_routes.router)
    application.include_router(routes.router)
    return application


def login(app, email):
    client = TestClient(app, base_url="https://testserver", headers=HEADERS)
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client


def fill_with_credentials(admin, data_dir: str) -> list[str]:
    endpoint = routes.endpoints.add("https://api.example.test/v1", kind="api-key", label="Hosted")
    routes.api_keys.save(endpoint.id, PROVIDER_KEY)
    assert admin.post("/api/email/accounts", json={
        "label": "Mail", "email_address": "drew@example.test", "password": MAIL_PASSWORD,
        "imap_host": "imap.example.test"}).status_code == 200
    routes.mcp_servers.add(name="GitHub", command="npx", args=["server-github"],
                           env={"GITHUB_PERSONAL_ACCESS_TOKEN": MCP_TOKEN})
    routes.mcp_servers.add(name="Remote", command="", args=[], transport="http",
                           url="https://mcp.example.test/mcp", headers={"Authorization": MCP_BEARER})
    assert admin.post("/api/notes", json={"title": "Keep me"}).status_code == 200
    with open(os.path.join(data_dir, ".env"), "w", encoding="utf-8") as handle:
        handle.write(f"ARTY_ACCESS_CLIENT_SECRET={DOTENV_SECRET}\n")
    with open(os.path.join(data_dir, "api_keys.json"), encoding="utf-8") as handle:
        api_keys_text = handle.read()
    with open(os.path.join(data_dir, "api_keys.json.unreadable-20261113-120000"), "w", encoding="utf-8") as handle:
        handle.write(api_keys_text)
    ciphertexts = list(json.loads(api_keys_text).values())
    for item in json.load(open(os.path.join(data_dir, "mcp_servers.json"), encoding="utf-8")):
        ciphertexts += list(item["env"].values()) + list(item["headers"].values())
    assert len(ciphertexts) == 4 and all(ciphertexts)
    return ciphertexts


def members_of(archive: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def zip_of(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def test_a_backup_carries_no_keys_passwords_tokens_or_sessions(tmp_path, monkeypatch):
    data_dir = str(tmp_path / "data")
    app = install(data_dir, monkeypatch, "admin@example.com")
    admin = login(app, "admin@example.com")
    ciphertexts = fill_with_credentials(admin, data_dir)

    response = admin.get("/api/backup")
    assert response.status_code == 200
    members = members_of(response.content)
    names = {os.path.basename(name) for name in members}
    assert ".key" not in names and "sessions.json" not in names and ".env" not in names
    assert not [name for name in names if "api_keys.json" in name]
    for name, content in members.items():
        for secret in (*FAKE_SECRETS, *ciphertexts):
            assert secret.encode() not in content, name

    users = json.loads(members["users.json"])
    assert users[0]["email"] == "admin@example.com" and users[0]["password_hash"].startswith("$2b$")
    servers = {item["name"]: item for item in json.loads(members["mcp_servers.json"])}
    assert servers["GitHub"]["env"] == {"GITHUB_PERSONAL_ACCESS_TOKEN": ""}
    assert servers["Remote"]["headers"] == {"Authorization": ""}
    assert servers["Remote"]["url"] == "https://mcp.example.test/mcp"


def test_a_backup_restored_on_a_new_install_brings_data_back_and_asks_for_secrets_again(tmp_path, monkeypatch):
    old_dir = str(tmp_path / "old")
    old = login(install(old_dir, monkeypatch, "admin@example.com"), "admin@example.com")
    fill_with_credentials(old, old_dir)
    archive = old.get("/api/backup").content

    new_dir = str(tmp_path / "new")
    fresh = login(install(new_dir, monkeypatch, "first@example.com"), "first@example.com")
    assert fresh.post("/api/restore", files={"file": ("backup.zip", archive, "application/zip")}).json() == {"ok": True}
    monkeypatch.setattr(routes, "mcp_servers", McpServerStore(new_dir))
    monkeypatch.setattr(auth_routes, "users", UserStore(new_dir))

    restored = login(app_for(new_dir, monkeypatch), "admin@example.com")
    assert [n["title"] for n in restored.get("/api/notes").json()["notes"]] == ["Keep me"]
    assert [e["label"] for e in restored.get("/api/model-endpoints").json()["endpoints"]] == ["Hosted"]
    assert routes.api_keys.load() == {}
    [account] = restored.get("/api/email/accounts").json()["accounts"]
    assert account["email_address"] == "drew@example.test" and account["password_missing"] is True
    servers = {s["name"]: s for s in restored.get("/api/mcp/servers").json()["servers"]}
    assert servers["GitHub"]["secrets_missing"] is True and servers["Remote"]["secrets_missing"] is True
    assert routes.mcp_servers.get(servers["GitHub"]["id"]).env == {"GITHUB_PERSONAL_ACCESS_TOKEN": ""}
    folders = restored.get(f"/api/email/{account['id']}/folders")
    assert folders.status_code == 500 and "no password" in folders.json()["detail"]


def app_for(data_dir: str, monkeypatch) -> FastAPI:
    users, sessions = UserStore(data_dir), SessionStore(data_dir)
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(routes, "registry", user_data.configure(data_dir))
    application = FastAPI()
    auth.install(application, users, sessions)
    application.include_router(auth_routes.router)
    application.include_router(routes.router)
    return application


def test_servers_and_accounts_with_their_secrets_are_not_flagged(tmp_path, monkeypatch):
    data_dir = str(tmp_path / "data")
    admin = login(install(data_dir, monkeypatch, "admin@example.com"), "admin@example.com")
    fill_with_credentials(admin, data_dir)
    [account] = admin.get("/api/email/accounts").json()["accounts"]
    assert account["password_missing"] is False
    assert [s["secrets_missing"] for s in admin.get("/api/mcp/servers").json()["servers"]] == [False, False]


def test_an_old_backup_with_key_files_restores_its_data_but_not_its_credentials(tmp_path, monkeypatch, caplog):
    data_dir = str(tmp_path / "data")
    admin = login(install(data_dir, monkeypatch, "admin@example.com"), "admin@example.com")
    routes.api_keys.save("current-endpoint", PROVIDER_KEY)
    key_before = open(os.path.join(data_dir, ".key"), "rb").read()
    keys_before = open(os.path.join(data_dir, "api_keys.json"), "rb").read()
    sessions_before = open(os.path.join(data_dir, "sessions.json"), "rb").read()
    old_backup = zip_of({
        ".key": b"b2xkLWluc3RhbGwta2V5LW5vdC1yZWFsLW5vdC1yZWFsLW5vdC1yZWFsMTI=",
        "api_keys.json": b'{"old-endpoint": "gAAAAAPLACEHOLDER"}',
        "sessions.json": b"{}",
        "users/.env": DOTENV_SECRET.encode(),
        "restored-note.txt": b"data from the old backup",
    })
    with caplog.at_level(logging.INFO, logger="routes"):
        response = admin.post("/api/restore", files={"file": ("backup.zip", old_backup, "application/zip")})
    assert response.json() == {"ok": True}
    assert open(os.path.join(data_dir, "restored-note.txt"), "rb").read() == b"data from the old backup"
    assert open(os.path.join(data_dir, ".key"), "rb").read() == key_before
    assert open(os.path.join(data_dir, "api_keys.json"), "rb").read() == keys_before
    assert open(os.path.join(data_dir, "sessions.json"), "rb").read() == sessions_before
    assert not os.path.exists(os.path.join(data_dir, "users", ".env"))
    assert routes.api_keys.load() == {"current-endpoint": PROVIDER_KEY}
    assert admin.get("/api/auth/me").status_code == 200
    assert ".key" in caplog.text and "api_keys.json" in caplog.text


DB_PASSWORD = "PLACEHOLDER-db-password"
FLAG_KEY = "PLACEHOLDER-flag-key"
QUERY_KEY = "PLACEHOLDER-query-key"


def test_credentials_in_mcp_arguments_and_urls_stay_out_of_backups(tmp_path, monkeypatch):
    data_dir = str(tmp_path / "data")
    admin = login(install(data_dir, monkeypatch, "admin@example.com"), "admin@example.com")
    secret_url = f"https://mcp.example.test/sse?api_key={QUERY_KEY}"
    routes.mcp_servers.add(name="Postgres", command="npx", args=[
        "server-postgres", f"postgresql://reader:{DB_PASSWORD}@db.example.test/x", "--api-key", FLAG_KEY])
    routes.mcp_servers.add(name=secret_url, command="", args=[], transport="sse", url=secret_url)

    archive = admin.get("/api/backup").content
    for name, content in members_of(archive).items():
        for secret in (DB_PASSWORD, FLAG_KEY, QUERY_KEY):
            assert secret.encode() not in content, name
    servers = {item["command"] or "remote": item for item in json.loads(members_of(archive)["mcp_servers.json"])}
    assert servers["npx"]["args"][0] == "server-postgres"
    assert servers["remote"]["url"].startswith("https://mcp.example.test/sse?api_key=")

    new_dir = str(tmp_path / "new")
    fresh = login(install(new_dir, monkeypatch, "first@example.com"), "first@example.com")
    assert fresh.post("/api/restore", files={"file": ("backup.zip", archive, "application/zip")}).json() == {"ok": True}
    monkeypatch.setattr(routes, "mcp_servers", McpServerStore(new_dir))
    monkeypatch.setattr(auth_routes, "users", UserStore(new_dir))
    restored = login(app_for(new_dir, monkeypatch), "admin@example.com")
    listed = restored.get("/api/mcp/servers").json()["servers"]
    assert len(listed) == 2 and all(server["secrets_missing"] for server in listed)
