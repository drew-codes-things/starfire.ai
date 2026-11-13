import json
import logging
import os
import shutil
import stat
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes
from api_key_manager import APIKeyManager
from mcp_servers_store import McpServerStore

TOKEN = "ghp_PLACEHOLDER-not-a-real-token"
BEARER = "Bearer PLACEHOLDER-not-a-real-bearer"


def stored_text(data_dir) -> str:
    with open(os.path.join(str(data_dir), "mcp_servers.json"), encoding="utf-8") as handle:
        return handle.read()


def stored_items(data_dir) -> list:
    return json.loads(stored_text(data_dir))


def write_plain_file(data_dir, items) -> None:
    with open(os.path.join(str(data_dir), "mcp_servers.json"), "w", encoding="utf-8") as handle:
        json.dump(items, handle)


PLAIN_SERVERS = [
    {"id": "github1", "name": "GitHub", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
     "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": TOKEN}, "enabled": True, "transport": "stdio", "url": "",
     "headers": {}},
    {"id": "remote1", "name": "Remote", "command": "", "args": [], "env": {}, "enabled": False,
     "transport": "http", "url": "https://mcp.example.test/mcp", "headers": {"Authorization": BEARER}},
    {"id": "fetch1", "name": "Fetch", "command": "uvx", "args": ["mcp-server-fetch"], "env": {},
     "enabled": True, "transport": "stdio", "url": "", "headers": {}},
]


def test_env_and_header_values_are_encrypted_on_disk_and_plain_in_use(tmp_path):
    store = McpServerStore(str(tmp_path))
    local = store.add(name="GitHub", command="npx", args=["server-github"],
                      env={"GITHUB_PERSONAL_ACCESS_TOKEN": TOKEN})
    remote = store.add(name="Remote", command="", args=[], transport="http",
                       url="https://mcp.example.test/mcp", headers={"Authorization": BEARER})
    assert local.env == {"GITHUB_PERSONAL_ACCESS_TOKEN": TOKEN}
    text = stored_text(tmp_path)
    assert TOKEN not in text and BEARER not in text
    on_disk = {item["id"]: item for item in stored_items(tmp_path)}
    assert set(on_disk[local.id]["env"]) == {"GITHUB_PERSONAL_ACCESS_TOKEN"}
    assert set(on_disk[remote.id]["headers"]) == {"Authorization"}
    reopened = McpServerStore(str(tmp_path))
    assert reopened.get(local.id).env == {"GITHUB_PERSONAL_ACCESS_TOKEN": TOKEN}
    assert reopened.get(remote.id).headers == {"Authorization": BEARER}


def test_changing_other_servers_keeps_secrets_readable(tmp_path):
    store = McpServerStore(str(tmp_path))
    kept = store.add(name="GitHub", command="npx", args=[], env={"GITHUB_PERSONAL_ACCESS_TOKEN": TOKEN})
    other = store.add(name="Fetch", command="uvx", args=["mcp-server-fetch"])
    assert store.set_enabled(kept.id, False)
    assert store.set_enabled(kept.id, True)
    assert store.delete(other.id)
    [server] = McpServerStore(str(tmp_path)).list()
    assert server.env == {"GITHUB_PERSONAL_ACCESS_TOKEN": TOKEN} and server.enabled
    assert TOKEN not in stored_text(tmp_path)


def test_a_plain_text_file_is_encrypted_on_first_load(tmp_path):
    write_plain_file(tmp_path, PLAIN_SERVERS)
    servers = {s.id: s for s in McpServerStore(str(tmp_path)).list()}
    assert servers["github1"].env == {"GITHUB_PERSONAL_ACCESS_TOKEN": TOKEN}
    assert servers["remote1"].headers == {"Authorization": BEARER}
    assert servers["remote1"].enabled is False and servers["fetch1"].args == ["mcp-server-fetch"]
    text = stored_text(tmp_path)
    assert TOKEN not in text and BEARER not in text
    assert stat.S_IMODE(os.stat(tmp_path / "mcp_servers.json").st_mode) == 0o600
    migrated = stored_text(tmp_path)
    again = {s.id: s for s in McpServerStore(str(tmp_path)).list()}
    assert again["github1"].env == {"GITHUB_PERSONAL_ACCESS_TOKEN": TOKEN}
    assert again["remote1"].headers == {"Authorization": BEARER}
    assert stored_text(tmp_path) == migrated


def test_secrets_from_another_install_are_flagged_quietly_and_kept_on_disk(tmp_path, caplog, capsys):
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    old = McpServerStore(str(old_dir))
    server = old.add(name="GitHub", command="npx", args=[], env={"GITHUB_PERSONAL_ACCESS_TOKEN": TOKEN})
    shutil.copy(old_dir / "mcp_servers.json", new_dir / "mcp_servers.json")
    ciphertext = stored_items(old_dir)[0]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"]

    with caplog.at_level(logging.WARNING, logger="mcp_servers_store"):
        [seen] = McpServerStore(str(new_dir)).list()
    assert seen.id == server.id and seen.name == "GitHub" and seen.env == {"GITHUB_PERSONAL_ACCESS_TOKEN": ""}
    assert "GITHUB_PERSONAL_ACCESS_TOKEN" in caplog.text and "GitHub" in caplog.text
    assert TOKEN not in caplog.text and ciphertext not in caplog.text
    printed = capsys.readouterr()
    assert printed.out == "" and printed.err == ""

    assert McpServerStore(str(new_dir)).set_enabled(server.id, False)
    assert stored_items(new_dir)[0]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == ciphertext
    shutil.copy(new_dir / "mcp_servers.json", old_dir / "mcp_servers.json")
    assert McpServerStore(str(old_dir)).get(server.id).env == {"GITHUB_PERSONAL_ACCESS_TOKEN": TOKEN}


def test_the_encryption_key_is_the_one_api_keys_use(tmp_path):
    McpServerStore(str(tmp_path)).add(name="GitHub", command="npx", args=[],
                                      env={"GITHUB_PERSONAL_ACCESS_TOKEN": TOKEN})
    ciphertext = stored_items(tmp_path)[0]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"]
    assert APIKeyManager(str(tmp_path)).decrypt_api_key(ciphertext) == TOKEN
    assert sorted(os.listdir(tmp_path)) == [".key", "mcp_servers.json"]


def test_the_api_never_returns_env_or_header_values(tmp_path, monkeypatch):
    async def fake_connect(server, auth=None):
        assert server.env == {"GITHUB_PERSONAL_ACCESS_TOKEN": TOKEN}
        return []

    monkeypatch.setattr(routes, "mcp_servers", McpServerStore(str(tmp_path)))
    monkeypatch.setattr(routes.mcp_manager, "connect_server", fake_connect)
    monkeypatch.setattr(routes, "_current_user_is_active_admin", lambda: True)
    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)
    added = client.post("/api/mcp/servers", json={"preset": "github",
                                                   "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": TOKEN}})
    assert added.status_code == 200 and TOKEN not in added.text
    assert set(added.json()) == {"id", "name", "command", "args", "enabled", "tool_count"}
    listed = client.get("/api/mcp/servers")
    assert TOKEN not in listed.text
    assert "env" not in listed.json()["servers"][0] and "headers" not in listed.json()["servers"][0]
    assert TOKEN not in stored_text(tmp_path)


def drop_the_marker(data_dir) -> None:
    items = stored_items(data_dir)
    for item in items:
        item.pop("secrets_encrypted", None)
    write_plain_file(data_dir, items)


def test_ciphertext_left_by_a_rollback_is_not_encrypted_a_second_time(tmp_path):
    store = McpServerStore(str(tmp_path))
    server = store.add(name="Remote", command="", args=[], transport="http",
                       url="https://mcp.example.test/mcp", env={"GITHUB_PERSONAL_ACCESS_TOKEN": TOKEN},
                       headers={"Authorization": BEARER})
    before = stored_items(tmp_path)[0]
    drop_the_marker(tmp_path)

    reloaded = McpServerStore(str(tmp_path)).get(server.id)
    assert reloaded.env == {"GITHUB_PERSONAL_ACCESS_TOKEN": TOKEN}
    assert reloaded.headers == {"Authorization": BEARER}
    after = stored_items(tmp_path)[0]
    assert after["env"] == before["env"] and after["headers"] == before["headers"]


def test_a_token_from_another_key_without_the_marker_is_flagged_not_passed_through(tmp_path):
    from mcp_servers_store import has_missing_secrets

    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    server = McpServerStore(str(old_dir)).add(name="GitHub", command="npx", args=[],
                                              env={"GITHUB_PERSONAL_ACCESS_TOKEN": TOKEN})
    shutil.copy(old_dir / "mcp_servers.json", new_dir / "mcp_servers.json")
    drop_the_marker(new_dir)
    foreign = stored_items(new_dir)[0]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"]

    seen = McpServerStore(str(new_dir)).get(server.id)
    assert seen.env == {"GITHUB_PERSONAL_ACCESS_TOKEN": ""}
    assert has_missing_secrets(seen)
    assert stored_items(new_dir)[0]["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == foreign


DB_PASSWORD = "PLACEHOLDER-db-password"
FLAG_KEY = "PLACEHOLDER-flag-key"
INLINE_TOKEN = "PLACEHOLDER-inline-token"
QUERY_KEY = "PLACEHOLDER-query-key"
SECRET_ARGS = ["-y", "server-postgres", f"postgresql://reader:{DB_PASSWORD}@db.example.test/x",
               "--api-key", FLAG_KEY, f"--token={INLINE_TOKEN}"]
SECRET_URL = f"https://mcp.example.test/sse?api_key={QUERY_KEY}&mode=fast"
ARG_AND_URL_SECRETS = (DB_PASSWORD, FLAG_KEY, INLINE_TOKEN, QUERY_KEY)


def test_arguments_and_urls_are_encrypted_on_disk_and_plain_in_use(tmp_path):
    store = McpServerStore(str(tmp_path))
    local = store.add(name="Postgres", command="npx", args=list(SECRET_ARGS))
    remote = store.add(name=SECRET_URL, command="", args=[], transport="sse", url=SECRET_URL)
    text = stored_text(tmp_path)
    for secret in ARG_AND_URL_SECRETS:
        assert secret not in text
    reopened = McpServerStore(str(tmp_path))
    assert reopened.get(local.id).args == SECRET_ARGS
    assert reopened.get(remote.id).url == SECRET_URL
    assert QUERY_KEY not in reopened.get(remote.id).name


def test_plain_arguments_and_urls_saved_by_older_code_are_encrypted_on_first_load(tmp_path):
    write_plain_file(tmp_path, [
        {"id": "pg1", "name": "Postgres", "command": "npx", "args": list(SECRET_ARGS), "env": {},
         "enabled": True, "transport": "stdio", "url": "", "headers": {}, "secrets_encrypted": True},
        {"id": "sse1", "name": SECRET_URL, "command": "", "args": [], "env": {}, "enabled": True,
         "transport": "sse", "url": SECRET_URL, "headers": {}, "secrets_encrypted": True},
    ])
    servers = {s.id: s for s in McpServerStore(str(tmp_path)).list()}
    assert servers["pg1"].args == SECRET_ARGS and servers["sse1"].url == SECRET_URL
    text = stored_text(tmp_path)
    for secret in ARG_AND_URL_SECRETS:
        assert secret not in text


def test_backup_copies_keep_the_shape_of_arguments_and_urls_without_their_credentials(tmp_path):
    from mcp_servers_store import SECRET_REMOVED, has_missing_secrets

    store = McpServerStore(str(tmp_path))
    store.add(name="Postgres", command="npx", args=list(SECRET_ARGS))
    store.add(name="Remote", command="", args=[], transport="sse", url=SECRET_URL)
    copies = {item["name"]: item for item in store.without_secret_values(stored_items(tmp_path))}
    assert copies["Postgres"]["args"] == [
        "-y", "server-postgres", f"postgresql://reader:{SECRET_REMOVED}@db.example.test/x",
        "--api-key", SECRET_REMOVED, f"--token={SECRET_REMOVED}"]
    assert copies["Remote"]["url"] == f"https://mcp.example.test/sse?api_key={SECRET_REMOVED}&mode=fast"
    write_plain_file(tmp_path, list(copies.values()))
    restored = {s.name: s for s in McpServerStore(str(tmp_path)).list()}
    assert has_missing_secrets(restored["Postgres"]) and has_missing_secrets(restored["Remote"])
