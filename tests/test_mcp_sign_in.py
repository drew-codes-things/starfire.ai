import io
import json
import logging
import os
import sys
import zipfile
from urllib.parse import parse_qs, urlencode, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import bcrypt
import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

import auth
import auth_routes
import mcp_oauth
import routes
import user_data
from api_key_manager import APIKeyManager
from auth import SessionStore, UserStore
from config import config
from mcp_manager import McpManager
from mcp_servers_store import McpServerStore, needs_sign_in
from oauth_fake_server import RunningFakeServer

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}
ORIGIN = "https://testserver"
CALLBACK = f"{ORIGIN}{mcp_oauth.CALLBACK_PATH}"


def fast_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()


@pytest.fixture
def fake():
    with RunningFakeServer() as server:
        yield server


@pytest.fixture
def data_dir(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    return str(folder)


@pytest.fixture
def app(data_dir, fake, monkeypatch):
    users, sessions = UserStore(data_dir), SessionStore(data_dir)
    monkeypatch.setattr(config, "data_dir", data_dir)
    monkeypatch.setattr(config, "public_base_url", "")
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(auth_routes, "known_addresses", auth.KnownAddresses(data_dir))
    monkeypatch.setattr(auth_routes, "account_limiter", auth.LoginLimiter())
    monkeypatch.setattr(routes, "registry", user_data.configure(data_dir))
    monkeypatch.setattr(routes, "api_keys", APIKeyManager(data_dir))
    monkeypatch.setattr(routes, "mcp_servers", McpServerStore(data_dir))
    monkeypatch.setattr(routes, "mcp_manager", McpManager())
    monkeypatch.setattr(routes, "pending_sign_ins", mcp_oauth.PendingSignIns())
    monkeypatch.setattr(routes, "SIGN_IN_SERVICES", {"fake": {"name": "Fake service", "url": fake.mcp_url}})
    monkeypatch.setattr(auth, "hash_password", fast_hash)
    users.upsert("admin@example.com", "Admin", "admin", fast_hash(PASSWORD))
    users.upsert("sam@example.com", "Sam", "user", fast_hash(PASSWORD))
    application = FastAPI()
    auth.install(application, users, sessions)
    application.include_router(auth_routes.router)
    application.include_router(routes.router)
    return application


def signed_in_client(app, email):
    client = TestClient(app, base_url=ORIGIN, headers=HEADERS)
    client.__enter__()
    response = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client


def close(client) -> None:
    for server in routes.mcp_servers.list():
        client.portal.call(routes.mcp_manager.disconnect, server.id)
    client.__exit__(None, None, None)


@pytest.fixture
def admin(app):
    client = signed_in_client(app, "admin@example.com")
    yield client
    close(client)


def start(client, **body) -> str:
    response = client.post("/api/mcp/servers/sign-in", json=body or {"service": "fake"}, headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    return response.json()["authorize_url"]


def query_of(url: str) -> dict:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


def approve(authorize_url: str) -> str:
    answer = httpx.get(authorize_url, follow_redirects=False)
    assert answer.status_code == 302, answer.text
    location = urlsplit(answer.headers["location"])
    assert f"{location.scheme}://{location.netloc}" == ORIGIN
    return f"{location.path}?{location.query}"


def finish(client, callback: str) -> dict:
    response = client.get(callback, follow_redirects=False)
    assert response.status_code == 303, response.text
    target = urlsplit(response.headers["location"])
    assert target.path == "/" and not target.netloc
    return query_of(response.headers["location"])


def sign_in(client, **body) -> str:
    result = finish(client, approve(start(client, **body)))
    assert result["mcp_sign_in"] == "connected", result
    return result["server"]


def listed(client, server_id: str) -> dict:
    [server] = [s for s in client.get("/api/mcp/servers").json()["servers"] if s["id"] == server_id]
    return server


def echo(client, server_id: str) -> str:
    return client.portal.call(routes.mcp_manager.call_tool, f"mcp__{server_id}__echo", {"text": "hi"})


def every_token(fake) -> list[str]:
    return [*fake.state.access_tokens, *fake.state.refresh_tokens]


def test_signing_in_to_a_listed_service_connects_it_and_its_tools_work(admin, fake):
    assert admin.get("/api/mcp/sign-in-services").json() == {
        "services": [{"id": "fake", "name": "Fake service", "host": "127.0.0.1"}]}
    authorize_url = start(admin)
    params = query_of(authorize_url)
    assert authorize_url.startswith(f"{fake.state.base_url}/authorize?")
    assert params["redirect_uri"] == CALLBACK and params["code_challenge_method"] == "S256"
    assert params["client_id"] in fake.state.clients and params["resource"] == fake.mcp_url

    result = finish(admin, approve(authorize_url))
    assert result["mcp_sign_in"] == "connected"
    server = listed(admin, result["server"])
    assert server["name"] == "Fake service" and server["transport"] == "http"
    assert server["connected"] is True and server["tool_count"] == 1
    assert server["signs_in"] is True and server["needs_sign_in"] is False and server["problem"] == ""
    assert echo(admin, result["server"]) == "echo: hi"


def test_tokens_are_encrypted_on_disk_and_absent_from_backups_listings_and_logs(admin, fake, data_dir, caplog):
    with caplog.at_level(logging.DEBUG):
        server_id = sign_in(admin)
        assert echo(admin, server_id) == "echo: hi"
    tokens = every_token(fake)
    assert len(tokens) == 2

    with open(os.path.join(data_dir, "mcp_servers.json"), encoding="utf-8") as handle:
        on_disk = handle.read()
    assert not any(token in on_disk for token in tokens)
    stored = McpServerStore(data_dir).get(server_id).oauth
    assert stored["access_token"] in fake.state.access_tokens and stored["refresh_token"] in fake.state.refresh_tokens

    listing = admin.get("/api/mcp/servers").text
    assert not any(token in listing for token in tokens)
    assert not any(token in caplog.text for token in tokens)

    backup = admin.get("/api/backup")
    assert backup.status_code == 200
    with zipfile.ZipFile(io.BytesIO(backup.content)) as archive:
        for name in archive.namelist():
            content = archive.read(name).decode("utf-8", "replace")
            assert not any(token in content for token in tokens), name
        [backed_up] = json.loads(archive.read("mcp_servers.json"))
    assert backed_up["oauth"]["access_token"] == "" and backed_up["oauth"]["refresh_token"] == ""
    assert backed_up["oauth"]["client_id"] == stored["client_id"]


def test_an_expiring_token_is_renewed_before_use_and_the_new_one_is_saved(admin, fake, data_dir):
    fake.state.access_lifetime = mcp_oauth.REFRESH_MARGIN_S // 2
    server_id = sign_in(admin)
    assert fake.state.grant_count("refresh_token") >= 1
    assert echo(admin, server_id) == "echo: hi"
    saved = McpServerStore(data_dir).get(server_id).oauth
    assert saved["access_token"] in fake.state.access_tokens and saved["refresh_token"] in fake.state.refresh_tokens


def test_a_401_mid_session_renews_the_token_and_the_tool_still_works(admin, fake, data_dir):
    server_id = sign_in(admin)
    first = McpServerStore(data_dir).get(server_id).oauth["access_token"]
    fake.state.revoke_access_tokens()
    assert echo(admin, server_id) == "echo: hi"
    assert fake.state.grant_count("refresh_token") == 1
    renewed = McpServerStore(data_dir).get(server_id).oauth["access_token"]
    assert renewed != first and renewed in fake.state.access_tokens


def test_a_refused_refresh_asks_for_a_new_sign_in_which_reuses_the_server_and_registration(admin, fake, data_dir):
    server_id = sign_in(admin)
    fake.state.revoke_access_tokens()
    fake.state.refuse_refresh = True
    with pytest.raises(Exception):
        echo(admin, server_id)
    server = listed(admin, server_id)
    assert server["needs_sign_in"] is True and server["connected"] is False
    assert "invalid_grant" in server["problem"]

    fake.state.refuse_refresh = False
    registered_clients = len(fake.state.clients)
    assert sign_in(admin, server_id=server_id) == server_id
    assert len(fake.state.clients) == registered_clients
    assert [s["id"] for s in admin.get("/api/mcp/servers").json()["servers"]] == [server_id]
    server = listed(admin, server_id)
    assert server["needs_sign_in"] is False and server["connected"] is True and server["problem"] == ""
    assert echo(admin, server_id) == "echo: hi"


def test_a_server_that_needs_signing_in_again_is_not_connected_at_chat_time(admin, fake, data_dir):
    server_id = sign_in(admin)
    admin.portal.call(routes.mcp_manager.disconnect, server_id)
    McpServerStore(data_dir).update_oauth(server_id, {"needs_sign_in": True, "problem": "the original reason"})
    server = McpServerStore(data_dir).get(server_id)
    assert needs_sign_in(server)
    with pytest.raises(Exception, match="needs signing in again"):
        admin.portal.call(routes.connect_mcp_server, server)
    assert listed(admin, server_id)["problem"] == "the original reason"


def test_a_refresh_refused_while_connecting_keeps_the_refusal_as_the_reason(admin, fake, data_dir):
    server_id = sign_in(admin)
    admin.portal.call(routes.mcp_manager.disconnect, server_id)
    McpServerStore(data_dir).update_oauth(server_id, {"expires_at": 1.0})
    fake.state.refuse_refresh = True
    with pytest.raises(Exception):
        admin.portal.call(routes.connect_mcp_server, McpServerStore(data_dir).get(server_id))
    server = listed(admin, server_id)
    assert server["needs_sign_in"] is True
    assert server["problem"] == "renewing the sign-in was refused (400, invalid_grant)"


def test_a_wrong_state_is_rejected_and_a_used_callback_cannot_be_replayed(admin, fake):
    callback = approve(start(admin))
    params = query_of(callback)
    tampered = f"{mcp_oauth.CALLBACK_PATH}?{urlencode({**params, 'state': 'not-the-state'})}"
    assert finish(admin, tampered) == {"mcp_sign_in": "failed",
                                       "reason": "this sign-in is unknown or was already used; "
                                                 "start it again from Settings"}
    assert finish(admin, callback)["mcp_sign_in"] == "connected"
    replay = finish(admin, callback)
    assert replay["mcp_sign_in"] == "failed" and "already used" in replay["reason"]
    assert fake.state.grant_count("authorization_code") == 1


def test_an_expired_sign_in_is_rejected(admin, fake, monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(routes, "pending_sign_ins", mcp_oauth.PendingSignIns(clock=lambda: clock["now"]))
    callback = approve(start(admin))
    clock["now"] += mcp_oauth.PENDING_LIFETIME_S + 1
    result = finish(admin, callback)
    assert result["mcp_sign_in"] == "failed" and "took too long" in result["reason"]
    assert admin.get("/api/mcp/servers").json()["servers"] == []
    assert fake.state.grant_count("authorization_code") == 0


def test_a_callback_arriving_in_another_session_is_rejected(app, admin, fake):
    callback = approve(start(admin))
    other = signed_in_client(app, "admin@example.com")
    try:
        result = finish(other, callback)
    finally:
        close(other)
    assert result["mcp_sign_in"] == "failed" and "different starfire session" in result["reason"]
    assert fake.state.grant_count("authorization_code") == 0


def test_only_admins_can_start_or_finish_a_sign_in(app, admin, fake):
    callback = approve(start(admin))
    sam = signed_in_client(app, "sam@example.com")
    try:
        refused = sam.post("/api/mcp/servers/sign-in", json={"service": "fake"}, headers={"Origin": ORIGIN})
        result = finish(sam, callback)
    finally:
        close(sam)
    assert refused.status_code == 403
    assert result == {"mcp_sign_in": "failed", "reason": "only an admin can add MCP servers"}
    assert finish(admin, callback)["mcp_sign_in"] == "connected"


def test_a_sign_in_the_person_refused_at_the_service_is_reported(admin, fake):
    state = query_of(start(admin))["state"]
    result = finish(admin, f"{mcp_oauth.CALLBACK_PATH}?{urlencode({'state': state, 'error': 'access_denied'})}")
    assert result == {"mcp_sign_in": "failed", "reason": "Fake service did not allow the sign-in (access_denied)"}


def test_an_answer_naming_another_issuer_is_rejected(admin, fake):
    callback = approve(start(admin))
    params = query_of(callback)
    result = finish(admin, f"{mcp_oauth.CALLBACK_PATH}?{urlencode({**params, 'iss': 'https://evil.example'})}")
    assert result["mcp_sign_in"] == "failed" and "sign-in server" in result["reason"]
    assert fake.state.grant_count("authorization_code") == 0


def test_a_custom_url_runs_the_same_discovery_and_must_be_https(admin, fake):
    server_id = sign_in(admin, url=fake.mcp_url, name="My server")
    assert listed(admin, server_id)["name"] == "My server"
    unnamed_id = sign_in(admin, url=fake.mcp_url)
    assert listed(admin, unnamed_id)["name"] == urlsplit(fake.mcp_url).netloc
    refused = admin.post("/api/mcp/servers/sign-in", json={"url": "http://mcp.example.test/mcp"},
                         headers={"Origin": ORIGIN})
    assert refused.status_code == 400 and "https://" in refused.json()["detail"]


def test_a_service_without_dynamic_registration_is_refused_with_its_reason(admin, fake):
    fake.state.offer_registration = False
    refused = admin.post("/api/mcp/servers/sign-in", json={"service": "fake"}, headers={"Origin": ORIGIN})
    assert refused.status_code == 502 and "register themselves" in refused.json()["detail"]


def test_the_callback_address_comes_from_the_browser_origin_or_the_configured_public_address(admin, monkeypatch):
    foreign = admin.post("/api/mcp/servers/sign-in", json={"service": "fake"},
                         headers={"Origin": "https://evil.example"})
    assert foreign.status_code == 400 and "different site" in foreign.json()["detail"]
    monkeypatch.setattr(config, "public_base_url", "https://starfire.example.test/")
    redirect_uri = query_of(start(admin))["redirect_uri"]
    assert redirect_uri == f"https://starfire.example.test{mcp_oauth.CALLBACK_PATH}"


def request_with(host: str, scheme: str = "https", origin: str = "") -> Request:
    headers = [(b"host", host.encode())] + ([(b"origin", origin.encode())] if origin else [])
    return Request({"type": "http", "method": "POST", "scheme": scheme, "server": ("testserver", 443),
                    "path": "/api/mcp/servers/sign-in", "query_string": b"", "headers": headers})


def test_the_callback_address_needs_a_plain_host_and_takes_the_scheme_the_browser_used(monkeypatch):
    monkeypatch.setattr(config, "public_base_url", "")
    assert routes.sign_in_callback_uri(request_with("192.168.1.16")) == f"https://192.168.1.16{mcp_oauth.CALLBACK_PATH}"
    behind_proxy = request_with("starfire.example.test", scheme="http", origin="https://starfire.example.test")
    assert routes.sign_in_callback_uri(behind_proxy) == f"https://starfire.example.test{mcp_oauth.CALLBACK_PATH}"
    for bad_host in ("evil.test/path", "a b", "evil.test@other", ""):
        with pytest.raises(HTTPException) as raised:
            routes.sign_in_callback_uri(request_with(bad_host))
        assert raised.value.status_code == 400
    monkeypatch.setattr(config, "public_base_url", "starfire.example.test")
    with pytest.raises(HTTPException) as raised:
        routes.sign_in_callback_uri(request_with("192.168.1.16"))
    assert raised.value.status_code == 500
