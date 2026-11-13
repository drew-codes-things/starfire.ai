import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

from mcp_servers_store import McpServerStore, needs_sign_in

ACCESS = "PLACEHOLDER-access-token"
REFRESH = "PLACEHOLDER-refresh-token"
CLIENT_SECRET = "PLACEHOLDER-client-secret"
OAUTH = {"issuer": "https://auth.example.test", "authorization_endpoint": "https://auth.example.test/authorize",
         "token_endpoint": "https://auth.example.test/token", "registration_endpoint": "", "resource":
         "https://mcp.example.test/mcp", "scope": "read", "client_id": "client-1", "client_secret": CLIENT_SECRET,
         "auth_method": "client_secret_post", "redirect_uri": "https://starfire.example.test/cb",
         "access_token": ACCESS, "refresh_token": REFRESH, "expires_at": 2000000000.0, "service": "custom",
         "needs_sign_in": False, "problem": ""}


def file_text(data_dir) -> str:
    with open(os.path.join(str(data_dir), "mcp_servers.json"), encoding="utf-8") as handle:
        return handle.read()


def add_signed_in(store: McpServerStore):
    return store.add(name="Signed in", command="", args=[], transport="http", url="https://mcp.example.test/mcp",
                     oauth=dict(OAUTH))


def test_sign_in_secrets_are_encrypted_on_disk_and_plain_in_use(tmp_path):
    server = add_signed_in(McpServerStore(str(tmp_path)))
    text = file_text(tmp_path)
    assert ACCESS not in text and REFRESH not in text and CLIENT_SECRET not in text
    assert "client-1" in text and "https://auth.example.test/token" in text
    reloaded = McpServerStore(str(tmp_path)).get(server.id)
    assert reloaded.oauth == OAUTH and not needs_sign_in(reloaded)


def test_plain_sign_in_secrets_left_on_disk_are_encrypted_when_read(tmp_path):
    item = {"id": "s1", "name": "Signed in", "command": "", "args": [], "env": {}, "enabled": True,
            "transport": "http", "url": "https://mcp.example.test/mcp", "headers": {}, "oauth": dict(OAUTH)}
    with open(tmp_path / "mcp_servers.json", "w", encoding="utf-8") as handle:
        json.dump([item], handle)
    assert McpServerStore(str(tmp_path)).get("s1").oauth["access_token"] == ACCESS
    assert ACCESS not in file_text(tmp_path) and REFRESH not in file_text(tmp_path)


def test_new_tokens_and_status_changes_are_saved_encrypted(tmp_path):
    store = McpServerStore(str(tmp_path))
    server = add_signed_in(store)
    assert store.update_oauth(server.id, {"access_token": "PLACEHOLDER-newer", "expires_at": 1.0})
    assert "PLACEHOLDER-newer" not in file_text(tmp_path)
    assert store.get(server.id).oauth["access_token"] == "PLACEHOLDER-newer"
    assert store.update_oauth(server.id, {"needs_sign_in": True, "problem": "renewing the sign-in was refused"})
    assert needs_sign_in(store.get(server.id))
    assert not store.update_oauth("missing", {"access_token": "x"})


def test_a_backup_copy_has_no_sign_in_secrets_and_restoring_it_here_keeps_the_live_ones(tmp_path):
    store = McpServerStore(str(tmp_path))
    server = add_signed_in(store)
    with open(tmp_path / "mcp_servers.json", encoding="utf-8") as handle:
        backed_up = store.without_secret_values(json.load(handle))
    text = json.dumps(backed_up)
    assert ACCESS not in text and REFRESH not in text and CLIENT_SECRET not in text
    assert backed_up[0]["oauth"]["client_id"] == "client-1"
    store.restore(backed_up)
    assert store.get(server.id).oauth["access_token"] == ACCESS


def test_a_backup_restored_on_another_install_asks_for_a_new_sign_in(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    store = McpServerStore(str(first))
    server = add_signed_in(store)
    with open(first / "mcp_servers.json", encoding="utf-8") as handle:
        backed_up = store.without_secret_values(json.load(handle))
    McpServerStore(str(second)).restore(backed_up)
    assert needs_sign_in(McpServerStore(str(second)).get(server.id))


def test_tokens_from_another_install_key_are_dropped_and_ask_for_a_new_sign_in(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    server = add_signed_in(McpServerStore(str(first)))
    shutil.copy(first / "mcp_servers.json", second / "mcp_servers.json")
    reloaded = McpServerStore(str(second)).get(server.id)
    assert reloaded.oauth["access_token"] == "" and needs_sign_in(reloaded)


def test_a_server_without_sign_in_never_needs_one(tmp_path):
    server = McpServerStore(str(tmp_path)).add(name="Plain", command="", args=[], transport="http",
                                               url="https://mcp.example.test/mcp")
    assert server.oauth == {} and not needs_sign_in(McpServerStore(str(tmp_path)).get(server.id))
