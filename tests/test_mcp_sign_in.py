import asyncio
import io
import json
import logging
import os
import sys
import threading
import time
import zipfile
from urllib.parse import parse_qs, urlencode, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import bcrypt
import httpx
import pytest
import uvicorn
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
from oauth_fake_server import FakeOAuthState, RunningFakeServer, _free_port, build_app

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


def test_a_token_endpoint_blip_fails_one_call_and_the_next_call_works(admin, fake, data_dir, monkeypatch):
    server_id = sign_in(admin)
    fake.state.revoke_access_tokens()
    real_refresh = mcp_oauth.refresh_tokens
    attempts = []

    async def blip_once(client, grant, clock=None):
        attempts.append(grant.tokens.refresh_token)
        if len(attempts) == 1:
            raise mcp_oauth.McpSignInError("could not reach the token address: ConnectTimeout")
        return await real_refresh(client, grant)

    monkeypatch.setattr(mcp_oauth, "refresh_tokens", blip_once)
    with pytest.raises(Exception):
        echo(admin, server_id)
    server = listed(admin, server_id)
    assert server["connected"] is True and server["needs_sign_in"] is False
    assert echo(admin, server_id) == "echo: hi"
    assert len(attempts) == 2 and routes.mcp_manager.is_connected(server_id)


def test_a_refused_refresh_drops_the_session_so_it_is_not_shown_as_connected(admin, fake, data_dir):
    server_id = sign_in(admin)
    fake.state.revoke_access_tokens()
    fake.state.refuse_refresh = True
    with pytest.raises(Exception):
        echo(admin, server_id)
    assert not routes.mcp_manager.is_connected(server_id)
    assert routes.mcp_manager.get_all_openai_schemas({server_id}) == []
    assert listed(admin, server_id)["needs_sign_in"] is True


def test_a_plain_url_server_that_went_away_is_dropped_and_reconnects_at_chat_time(admin):
    def serve(state, port):
        server = uvicorn.Server(uvicorn.Config(build_app(state), host="127.0.0.1", port=port, log_level="warning",
                                               lifespan="off"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        while not server.started:
            time.sleep(0.02)
        return server, thread

    port = _free_port()
    state = FakeOAuthState(base_url=f"http://127.0.0.1:{port}")
    state.access_tokens["static-token"] = time.time() + 3600
    running, thread = serve(state, port)
    server = routes.mcp_servers.add(name="Plain", command="", args=[], transport="http", url=f"{state.base_url}/mcp",
                                    headers={"Authorization": "Bearer static-token"})
    try:
        admin.portal.call(routes.connect_mcp_server, server)
        assert echo(admin, server.id) == "echo: hi"
        running.should_exit = True
        thread.join(timeout=10)
        with pytest.raises(Exception):
            echo(admin, server.id)
        assert not routes.mcp_manager.is_connected(server.id)
        running, thread = serve(state, port)
        admin.portal.call(routes.connect_mcp_server, routes.mcp_servers.get(server.id))
        assert echo(admin, server.id) == "echo: hi"
    finally:
        admin.portal.call(routes.mcp_manager.disconnect, server.id)
        running.should_exit = True
        thread.join(timeout=10)


def test_sign_in_again_registers_afresh_when_the_service_rejects_the_client(admin, fake, data_dir):
    server_id = sign_in(admin)
    old_client = McpServerStore(data_dir).get(server_id).oauth["client_id"]
    fake.state.clients.clear()
    fake.state.revoke_access_tokens()
    with pytest.raises(Exception):
        echo(admin, server_id)
    server = listed(admin, server_id)
    assert server["needs_sign_in"] is True and "invalid_client" in server["problem"]
    authorize_url = start(admin, server_id=server_id)
    assert query_of(authorize_url)["client_id"] != old_client
    assert finish(admin, approve(authorize_url)) == {"mcp_sign_in": "connected", "server": server_id}
    assert echo(admin, server_id) == "echo: hi"


def test_a_sign_in_again_that_never_came_back_registers_afresh_next_time(admin, fake, data_dir):
    server_id = sign_in(admin)
    McpServerStore(data_dir).update_oauth(server_id, {"needs_sign_in": True})
    old_client = McpServerStore(data_dir).get(server_id).oauth["client_id"]
    fake.state.clients.clear()
    abandoned = start(admin, server_id=server_id)
    assert query_of(abandoned)["client_id"] == old_client
    assert httpx.get(abandoned, follow_redirects=False).status_code == 400
    retried = start(admin, server_id=server_id)
    assert query_of(retried)["client_id"] != old_client
    assert finish(admin, approve(retried))["mcp_sign_in"] == "connected"
    assert McpServerStore(data_dir).get(server_id).oauth["reregister"] is False
    assert query_of(start(admin, server_id=server_id))["client_id"] == query_of(retried)["client_id"]


def test_a_registration_that_needs_a_secret_it_no_longer_has_is_not_reused(admin, fake, data_dir):
    server_id = sign_in(admin)
    store = McpServerStore(data_dir)
    store.update_oauth(server_id, {"auth_method": "client_secret_post", "client_secret": ""})
    existing = store.get(server_id)
    discovered = mcp_oauth.OAuthGrant.from_record(existing.oauth).server
    assert routes._reusable_registration(existing, discovered, CALLBACK) is None
    store.update_oauth(server_id, {"client_secret": "PLACEHOLDER-secret"})
    assert routes._reusable_registration(store.get(server_id), discovered, CALLBACK).client_id == \
        existing.oauth["client_id"]


def test_people_who_are_not_admins_see_a_generic_problem_without_addresses(app, admin, fake, data_dir):
    server_id = sign_in(admin)
    detail = "failed to connect to 'http://10.0.0.5:8080/tenant-4711/private-path/mcp': Not Found"
    McpServerStore(data_dir).update_oauth(server_id, {"problem": detail})
    assert listed(admin, server_id)["problem"] == detail
    sam = signed_in_client(app, "sam@example.com")
    try:
        [view] = sam.get("/api/mcp/servers").json()["servers"]
    finally:
        close(sam)
    assert view["problem"] == routes.HIDDEN_PROBLEM and "tenant-4711" not in json.dumps(view)


def test_a_damaged_saved_sign_in_is_a_connection_error_that_asks_for_a_new_sign_in(admin, data_dir):
    store = McpServerStore(data_dir)
    server = store.add(name="Broken", command="", args=[], transport="http", url="https://mcp.example.test/mcp",
                       oauth={"access_token": "x"})
    with pytest.raises(routes.McpConnectionError, match="saved sign-in"):
        admin.portal.call(routes.connect_mcp_server, store.get(server.id))
    view = listed(admin, server.id)
    assert view["needs_sign_in"] is True and "saved sign-in" in view["problem"]


def test_a_saved_sign_in_is_never_sent_to_an_address_it_was_not_issued_for(admin, fake, data_dir):
    server_id = sign_in(admin)
    admin.portal.call(routes.mcp_manager.disconnect, server_id)
    fake.state.mcp_authorizations.clear()
    moved = McpServerStore(data_dir).get(server_id)
    moved.url = moved.url.replace("/mcp", "/elsewhere/mcp")
    with pytest.raises(routes.McpConnectionError, match="new address"):
        admin.portal.call(routes.connect_mcp_server, moved)
    assert fake.state.mcp_authorizations == []
    assert listed(admin, server_id)["needs_sign_in"] is True


def test_a_sign_in_server_that_promises_iss_must_send_it(admin, fake):
    fake.state.advertise_iss = True
    params = query_of(approve(start(admin)))
    without_iss = f"{mcp_oauth.CALLBACK_PATH}?{urlencode({'code': params['code'], 'state': params['state']})}"
    result = finish(admin, without_iss)
    assert result["mcp_sign_in"] == "failed" and "did not say" in result["reason"]
    assert fake.state.grant_count("authorization_code") == 0
    assert sign_in(admin)


def test_starting_a_sign_in_names_the_host_the_browser_will_be_sent_to(admin, fake):
    answer = admin.post("/api/mcp/servers/sign-in", json={"service": "fake"}, headers={"Origin": ORIGIN}).json()
    assert answer["server_host"] == "127.0.0.1" and answer["sign_in_host"] == "127.0.0.1"


def test_a_sign_in_flow_that_runs_past_the_deadline_is_refused(admin, fake, monkeypatch):
    monkeypatch.setattr(mcp_oauth, "FLOW_DEADLINE_S", 0.3, raising=False)
    real_discover = mcp_oauth.discover

    async def slow_discover(client, url):
        await asyncio.sleep(2)
        return await real_discover(client, url)

    monkeypatch.setattr(mcp_oauth, "discover", slow_discover)
    refused = admin.post("/api/mcp/servers/sign-in", json={"service": "fake"}, headers={"Origin": ORIGIN})
    assert refused.status_code == 502 and "took longer than 0.3 seconds" in refused.json()["detail"]
