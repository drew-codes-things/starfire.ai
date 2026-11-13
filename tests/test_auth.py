import ipaddress
import os
import stat
import sys

import bcrypt
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import auth
from auth import SESSION_COOKIE, LoginLimiter, SessionStore, UserStore

PASSWORD = "correct horse battery staple"


def read_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def fast_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()


@pytest.fixture
def users(tmp_path):
    return UserStore(str(tmp_path))


@pytest.fixture
def sessions(tmp_path):
    return SessionStore(str(tmp_path))


def build_app(users, sessions):
    app = FastAPI()
    auth.install(app, users, sessions)

    @app.get("/api/secret")
    def secret(request: Request):
        return {"email": request.state.user.email}

    @app.get("/index.html")
    def page():
        return {"page": True}

    @app.get("/login.html")
    def login_page():
        return {"login": True}

    return app


def test_verify_accepts_the_right_password_and_ignores_email_case(users):
    users.upsert("Drew@Example.com", "Drew", "admin", fast_hash(PASSWORD))
    assert users.verify("drew@example.com ", PASSWORD).name == "Drew"
    assert users.verify("drew@example.com", "wrong") is None
    assert users.verify("nobody@example.com", PASSWORD) is None


def test_upsert_keeps_one_account_per_email(users):
    first = users.upsert("drew@example.com", "Drew", "admin", fast_hash("old"))
    second = users.upsert("drew@example.com", "Drew", "admin", fast_hash(PASSWORD))
    assert first.id == second.id
    assert len(users.list()) == 1
    assert users.verify("drew@example.com", PASSWORD)


@pytest.mark.parametrize("role, password_hash, email", [
    ("pending", None, "a@b.c"),
    ("admin", "plaintext", "a@b.c"),
    ("admin", None, "not-an-email"),
])
def test_upsert_rejects_bad_accounts(users, role, password_hash, email):
    with pytest.raises(ValueError):
        users.upsert(email, "x", role, password_hash or fast_hash(PASSWORD))


def test_users_file_is_private(users, tmp_path):
    users.upsert("drew@example.com", "Drew", "admin", fast_hash(PASSWORD))
    assert os.stat(tmp_path / "users.json").st_mode & 0o077 == 0


def test_sessions_store_only_a_digest_and_can_be_revoked(sessions, tmp_path):
    token = sessions.create("user-1")
    assert sessions.user_id_for(token) == "user-1"
    assert token not in (tmp_path / "sessions.json").read_text()
    sessions.revoke(token)
    assert sessions.user_id_for(token) is None
    assert sessions.user_id_for("") is None


def test_expired_sessions_are_rejected(sessions, monkeypatch):
    token = sessions.create("user-1")
    monkeypatch.setattr(auth.time, "time", lambda: 10 ** 12)
    assert sessions.user_id_for(token) is None


def test_limiter_locks_after_repeated_failures_and_resets():
    limiter = LoginLimiter(max_failures=3, lockout_seconds=60)
    for _ in range(2):
        limiter.record_failure("ip|drew")
    assert limiter.seconds_locked("ip|drew") == 0
    limiter.record_failure("ip|drew")
    assert limiter.seconds_locked("ip|drew") > 0
    assert limiter.seconds_locked("other|drew") == 0
    limiter.reset("ip|drew")
    assert limiter.seconds_locked("ip|drew") == 0


def test_only_setup_is_reachable_until_an_account_exists(users, sessions):
    client = TestClient(build_app(users, sessions))
    for page in ("/index.html", "/login.html"):
        response = client.get(page, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/setup.html"
    response = client.get("/api/secret")
    assert response.status_code == 401 and response.json()["setup"] is True


def test_setup_is_closed_once_an_account_exists(users, sessions):
    users.upsert("drew@example.com", "Drew", "admin", fast_hash(PASSWORD))
    client = TestClient(build_app(users, sessions))
    page = client.get("/setup.html", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/login.html"
    assert client.post("/api/auth/setup", json={}).status_code == 409


def test_login_is_required_once_an_account_exists(users, sessions):
    user = users.upsert("drew@example.com", "Drew", "admin", fast_hash(PASSWORD))
    client = TestClient(build_app(users, sessions))

    assert client.get("/api/secret").status_code == 401
    page = client.get("/index.html", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/login.html"
    assert client.get("/login.html").status_code == 200

    client.cookies.set(SESSION_COOKIE, sessions.create(user.id, "testserver"))
    assert client.get("/api/secret").json() == {"email": "drew@example.com"}


def test_forwarded_address_is_only_trusted_from_the_local_proxy():
    def request_from(peer, forwarded):
        scope = {"type": "http", "client": (peer, 1234), "headers": [(b"x-forwarded-for", forwarded.encode())]}
        return Request(scope)

    assert auth.client_address(request_from("127.0.0.1", "203.0.113.5")) == "203.0.113.5"
    assert auth.client_address(request_from("192.168.1.50", "203.0.113.5")) == "192.168.1.50"


def test_the_address_is_the_one_the_local_proxy_added_not_one_the_client_sent():
    def request_from(forwarded):
        scope = {"type": "http", "client": ("127.0.0.1", 1234), "headers": [(b"x-forwarded-for", forwarded.encode())]}
        return Request(scope)

    assert auth.client_address(request_from("6.6.6.6, 203.0.113.5")) == "203.0.113.5"
    assert auth.client_address(request_from("7.7.7.7,203.0.113.5 ")) == "203.0.113.5"


@pytest.fixture
def login_client(users, sessions, monkeypatch, tmp_path):
    import auth_routes
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(auth_routes, "limiter", LoginLimiter(max_failures=2, lockout_seconds=60))
    monkeypatch.setattr(auth_routes, "account_limiter", LoginLimiter())
    monkeypatch.setattr(auth_routes, "known_addresses", auth.KnownAddresses(str(tmp_path)))
    users.upsert("drew@example.com", "Drew", "admin", fast_hash(PASSWORD))
    app = build_app(users, sessions)
    app.include_router(auth_routes.router)
    return TestClient(app, base_url="https://testserver")


def test_login_route_sets_a_locked_down_cookie_and_logout_ends_it(login_client):
    response = login_client.post("/api/auth/login", json={"email": "drew@example.com", "password": PASSWORD})
    assert response.status_code == 200
    assert {k: response.json()[k] for k in ("email", "name", "role")} == {"email": "drew@example.com", "name": "Drew", "role": "admin"}
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie

    assert login_client.get("/api/auth/me").json()["email"] == "drew@example.com"
    assert login_client.get("/api/secret").status_code == 200

    login_client.post("/api/auth/logout")
    assert login_client.get("/api/secret").status_code == 401


def test_login_route_rejects_wrong_passwords_then_locks(login_client):
    wrong = {"email": "drew@example.com", "password": "nope"}
    assert login_client.post("/api/auth/login", json=wrong).status_code == 401
    assert login_client.post("/api/auth/login", json=wrong).status_code == 401
    right = {"email": "drew@example.com", "password": PASSWORD}
    assert login_client.post("/api/auth/login", json=right).status_code == 429


def test_login_route_rejects_oversized_input(login_client):
    response = login_client.post("/api/auth/login", json={"email": "a" * 300, "password": PASSWORD})
    assert response.status_code == 400


@pytest.fixture
def setup_client(users, sessions, monkeypatch):
    import auth_routes
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    app = build_app(users, sessions)
    app.include_router(auth_routes.router)
    return TestClient(app, base_url="https://testserver")


def setup_body(**changes):
    return {"email": "Drew@Example.com", "name": "Drew", "password": PASSWORD, "confirm": PASSWORD, **changes}


@pytest.mark.parametrize("changes, message", [
    ({"email": "not-an-email"}, "valid email"),
    ({"password": "short", "confirm": "short"}, "at least 10"),
    ({"confirm": PASSWORD + "!"}, "don't match"),
])
def test_setup_refuses_bad_details(setup_client, users, changes, message):
    response = setup_client.post("/api/auth/setup", json=setup_body(**changes))
    assert response.status_code == 400 and message in response.json()["detail"]
    assert not users.has_users()


def test_setup_creates_one_admin_and_logs_in(setup_client, users):
    assert setup_client.get("/api/auth/status").json() == {"has_users": False}
    response = setup_client.post("/api/auth/setup", json=setup_body())
    assert {k: response.json()[k] for k in ("email", "name", "role")} == {"email": "drew@example.com", "name": "Drew", "role": "admin"}
    assert setup_client.get("/api/secret").status_code == 200
    assert PASSWORD not in read_text(users.path)
    again = setup_client.post("/api/auth/setup", json=setup_body(email="evil@example.com"))
    assert again.status_code == 409
    assert [u.email for u in users.list()] == ["drew@example.com"]


def test_changing_the_password_logs_out_other_sessions(setup_client, users, sessions):
    setup_client.post("/api/auth/setup", json=setup_body())
    user = users.list()[0]
    other_token = sessions.create(user.id)
    new = "a brand new password"

    wrong = setup_client.post("/api/auth/password", json={"current": "nope", "password": new, "confirm": new})
    assert wrong.status_code == 400
    changed = setup_client.post("/api/auth/password", json={"current": PASSWORD, "password": new, "confirm": new})
    assert changed.status_code == 200

    assert sessions.user_id_for(other_token) is None
    assert setup_client.get("/api/secret").status_code == 200
    assert users.verify("drew@example.com", new) and not users.verify("drew@example.com", PASSWORD)


def test_health_check_needs_no_login():
    assert auth.is_public("/api/health")


@pytest.mark.parametrize("path", ["/vendor/fonts.css", "/vendor/fonts/ibm-plex-sans-latin-400-normal.woff2"])
def test_fonts_load_on_the_login_and_setup_pages(path):
    assert auth.is_public(path)


@pytest.mark.parametrize("path", ["/vendor/marked.min.js", "/app.js", "/workspace.js", "/index.html", "/vendor/",
                                  "/appState.js", "/chatMessages.js", "/codeBlocks.js", "/chatCards.js", "/chatSend.js",
                                  "/helperTasks.js", "/settings.js", "/settingsMemoryTasks.js", "/settingsEmail.js",
                                  "/settingsNotes.js", "/settingsSystem.js", "/compare.js", "/palette.js"])
def test_app_code_still_needs_a_login(path):
    assert not auth.is_public(path)


def test_a_page_asked_for_before_login_opens_again_after_it(users, sessions):
    users.upsert("drew@example.com", "Drew", "admin", fast_hash(PASSWORD))
    app = build_app(users, sessions)

    @app.get("/arty-devices.html")
    def devices_page():
        return {"page": True}

    client = TestClient(app)
    page = client.get("/arty-devices.html?code=ABCD-EFGH", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/login.html?next=%2Farty-devices.html%3Fcode%3DABCD-EFGH"
    home = client.get("/index.html", follow_redirects=False)
    assert home.headers["location"] == "/login.html"


def test_the_arty_devices_api_is_admin_only_even_to_read():
    assert auth.is_admin_only("GET", "/api/arty-devices")
    assert auth.is_admin_only("GET", "/api/arty-devices/pending")
    assert not auth.is_admin_only("GET", "/api/model-endpoints")


def test_only_admins_can_list_the_server_folders():
    assert auth.is_admin_only("GET", "/api/browse-dir")


def test_the_login_page_can_read_where_to_go_next():
    assert auth.is_public("/loginNext.js")


def test_logging_out_keeps_the_sessions_file_private(tmp_path):
    sessions = SessionStore(str(tmp_path))
    token = sessions.create("user-1")
    sessions.create("user-2")
    os.chmod(sessions.path, 0o644)
    sessions.revoke(token)
    assert stat.S_IMODE(os.stat(sessions.path).st_mode) == 0o600


LONG_PASSWORD = "a long passphrase that keeps going " * 3


def test_a_login_with_a_password_longer_than_bcrypt_reads_is_refused_not_a_crash(login_client):
    response = login_client.post("/api/auth/login", json={"email": "drew@example.com", "password": LONG_PASSWORD})
    assert response.status_code == 401


def test_a_long_password_set_before_bcrypt_5_still_logs_in(users):
    first_bytes_only = bcrypt.hashpw(LONG_PASSWORD.encode()[:auth.MAX_PASSWORD_BYTES], bcrypt.gensalt(rounds=4)).decode()
    users.upsert("drew@example.com", "Drew", "admin", first_bytes_only)
    assert users.verify("drew@example.com", LONG_PASSWORD).name == "Drew"


def test_new_passwords_must_fit_in_what_bcrypt_reads():
    with pytest.raises(auth.AccountError, match="too long"):
        auth.check_new_password(LONG_PASSWORD, LONG_PASSWORD)
    with pytest.raises(auth.AccountError, match="too long"):
        auth.check_new_password("é" * 40, "é" * 40)
    auth.check_new_password("x" * 72, "x" * 72)


TUNNEL = "192.168.1.10"
BEHIND_TUNNEL = tuple(ipaddress.ip_network(n) for n in ("127.0.0.1", "::1", TUNNEL))


def proxied_request(peer, forwarded="", real=""):
    headers = []
    if forwarded:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    if real:
        headers.append((b"cf-connecting-ip", real.encode()))
    return Request({"type": "http", "client": (peer, 1234), "headers": headers})


def test_a_visitor_through_the_tunnel_is_known_by_the_address_cloudflare_saw():
    request = proxied_request("127.0.0.1", TUNNEL, "203.0.113.9")
    assert auth.client_address(request, BEHIND_TUNNEL) == "203.0.113.9"


def test_a_lan_client_cannot_pick_its_address_with_the_cloudflare_header():
    request = proxied_request("127.0.0.1", "192.168.1.50", "203.0.113.9")
    assert auth.client_address(request, BEHIND_TUNNEL) == "192.168.1.50"


def test_a_peer_that_is_not_a_trusted_proxy_is_used_as_it_is():
    request = proxied_request("192.168.1.77", TUNNEL, "203.0.113.9")
    assert auth.client_address(request, BEHIND_TUNNEL) == "192.168.1.77"


def test_a_tunnel_request_without_a_usable_cloudflare_header_counts_as_the_tunnel():
    assert auth.client_address(proxied_request("127.0.0.1", TUNNEL, "not-an-address"), BEHIND_TUNNEL) == TUNNEL
    assert auth.client_address(proxied_request("127.0.0.1", TUNNEL), BEHIND_TUNNEL) == TUNNEL


def test_a_proxy_chain_that_already_names_the_visitor_is_followed_past_trusted_hops():
    request = proxied_request("127.0.0.1", f"6.6.6.6, 203.0.113.9, {TUNNEL}", "198.51.100.1")
    assert auth.client_address(request, BEHIND_TUNNEL) == "203.0.113.9"


def test_the_cloudflare_header_is_ignored_unless_the_tunnel_is_trusted():
    request = proxied_request("127.0.0.1", TUNNEL, "203.0.113.9")
    assert auth.client_address(request) == TUNNEL


def test_trusted_proxies_must_be_addresses_or_networks():
    assert auth.parse_networks(" 127.0.0.1 , 10.0.0.0/8,,::1 ") == tuple(
        ipaddress.ip_network(n) for n in ("127.0.0.1/32", "10.0.0.0/8", "::1/128"))
    with pytest.raises(ValueError, match="cloudflared"):
        auth.parse_networks("127.0.0.1,cloudflared")


def test_a_stranger_through_the_tunnel_cannot_lock_the_owner_out(login_client, monkeypatch):
    import auth_routes
    monkeypatch.setattr(auth_routes, "TRUSTED_PROXIES", BEHIND_TUNNEL, raising=False)
    owner = TestClient(login_client.app, base_url="https://testserver", client=("127.0.0.1", 50001))
    stranger = TestClient(login_client.app, base_url="https://testserver", client=("127.0.0.1", 50002))
    wrong = {"email": "drew@example.com", "password": "nope"}
    for _ in range(3):
        stranger.post("/api/auth/login", json=wrong, headers={"X-Forwarded-For": TUNNEL, "CF-Connecting-IP": "203.0.113.66"})
    right = {"email": "drew@example.com", "password": PASSWORD}
    response = owner.post("/api/auth/login", json=right, headers={"X-Forwarded-For": TUNNEL, "CF-Connecting-IP": "198.51.100.7"})
    assert response.status_code == 200


@pytest.fixture
def account_client(login_client, monkeypatch):
    import auth_routes
    monkeypatch.setattr(auth_routes, "TRUSTED_PROXIES", BEHIND_TUNNEL)
    monkeypatch.setattr(auth_routes, "account_limiter", LoginLimiter(max_failures=3, lockout_seconds=3600))
    return login_client


def sign_in_from(app, address, password=PASSWORD, cookie=None):
    client = TestClient(app, base_url="https://testserver", client=("127.0.0.1", 50000))
    if cookie:
        client.cookies.set(SESSION_COOKIE, cookie)
    response = client.post("/api/auth/login", json={"email": "drew@example.com", "password": password},
                           headers={"X-Forwarded-For": TUNNEL, "CF-Connecting-IP": address})
    return response


def test_wrong_passwords_from_many_addresses_lock_the_account_for_new_addresses(account_client):
    for address in ("203.0.113.1", "203.0.113.2", "203.0.113.3"):
        assert sign_in_from(account_client.app, address, "nope").status_code == 401
    assert sign_in_from(account_client.app, "198.51.100.20").status_code == 429


def test_the_owner_still_gets_in_from_an_address_they_signed_in_from_before(account_client):
    assert sign_in_from(account_client.app, "198.51.100.7").status_code == 200
    for address in ("203.0.113.1", "203.0.113.2", "203.0.113.3", "203.0.113.4"):
        sign_in_from(account_client.app, address, "nope")
    assert sign_in_from(account_client.app, "198.51.100.7").status_code == 200


def test_the_owner_still_gets_in_with_a_session_they_already_hold(account_client):
    session = sign_in_from(account_client.app, "198.51.100.7").cookies.get(SESSION_COOKIE)
    for address in ("203.0.113.1", "203.0.113.2", "203.0.113.3"):
        sign_in_from(account_client.app, address, "nope")
    assert sign_in_from(account_client.app, "192.0.2.44").status_code == 429
    assert sign_in_from(account_client.app, "192.0.2.44", cookie=session).status_code == 200


def test_wrong_passwords_never_make_an_address_known(account_client):
    for _ in range(2):
        sign_in_from(account_client.app, "203.0.113.1", "nope")
    sign_in_from(account_client.app, "203.0.113.2", "nope")
    assert sign_in_from(account_client.app, "203.0.113.1").status_code == 429
    assert sign_in_from(account_client.app, "203.0.113.2").status_code == 429


def test_known_addresses_are_private_and_forgotten_after_a_while(tmp_path, monkeypatch):
    known = auth.KnownAddresses(str(tmp_path))
    known.remember("drew@example.com", "198.51.100.7")
    assert known.is_known("drew@example.com", "198.51.100.7")
    assert not known.is_known("drew@example.com", "198.51.100.8")
    assert not known.is_known("sam@example.com", "198.51.100.7")
    assert stat.S_IMODE(os.stat(known.path).st_mode) == 0o600
    later = auth.time.time() + auth.KNOWN_ADDRESS_SECONDS + 1
    monkeypatch.setattr(auth.time, "time", lambda: later)
    assert not known.is_known("drew@example.com", "198.51.100.7")


def test_only_the_most_recent_known_addresses_are_kept(tmp_path, monkeypatch):
    known = auth.KnownAddresses(str(tmp_path))
    clock = [1_000_000.0]
    monkeypatch.setattr(auth.time, "time", lambda: clock[0])
    for index in range(auth.MAX_KNOWN_ADDRESSES + 5):
        clock[0] += 1
        known.remember("drew@example.com", f"198.51.100.{index}")
    assert not known.is_known("drew@example.com", "198.51.100.0")
    assert known.is_known("drew@example.com", f"198.51.100.{auth.MAX_KNOWN_ADDRESSES + 4}")


def test_the_limiter_forgets_failures_once_their_lockout_window_has_passed(monkeypatch):
    limiter = LoginLimiter(max_failures=3, lockout_seconds=60)
    clock = [1_000_000.0]
    monkeypatch.setattr(auth.time, "time", lambda: clock[0])
    for index in range(300):
        limiter.record_failure(f"203.0.113.1|n{index}@example.com")
    clock[0] += 61
    limiter.record_failure("203.0.113.1|drew@example.com")
    assert len(limiter._failures) == 1


def test_the_limiter_keeps_the_newest_keys_when_it_is_full(monkeypatch):
    limiter = LoginLimiter(max_failures=1, lockout_seconds=60, max_keys=3)
    clock = [1_000_000.0]
    monkeypatch.setattr(auth.time, "time", lambda: clock[0])
    for index in range(5):
        clock[0] += 1
        limiter.record_failure(f"key-{index}")
    assert len(limiter._failures) == 3
    assert limiter.seconds_locked("key-4") > 0
    assert limiter.seconds_locked("key-0") == 0


LOCAL_CHECK_HOST = "127.0.0.1:8090"


@pytest.fixture
def hosts_app(users, monkeypatch, tmp_path):
    import auth_routes
    bound = SessionStore(str(tmp_path), identity_check_hosts=(LOCAL_CHECK_HOST,))
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", bound)
    monkeypatch.setattr(auth_routes, "limiter", LoginLimiter())
    monkeypatch.setattr(auth_routes, "account_limiter", LoginLimiter())
    monkeypatch.setattr(auth_routes, "known_addresses", auth.KnownAddresses(str(tmp_path)))
    users.upsert("drew@example.com", "Drew", "admin", fast_hash(PASSWORD))
    app = build_app(users, bound)
    app.include_router(auth_routes.router)
    app.state.sessions = bound
    return app


def session_made_on(app, host):
    client = TestClient(app, base_url=f"https://{host}")
    response = client.post("/api/auth/login", json={"email": "drew@example.com", "password": PASSWORD})
    assert response.status_code == 200
    return response.cookies.get(SESSION_COOKIE)


def ask_with(app, host, path, token):
    client = TestClient(app, base_url=f"https://{host}")
    return client.get(path, headers={"Cookie": f"{SESSION_COOKIE}={token}"})


def test_a_session_only_opens_the_host_it_was_made_on(hosts_app):
    token = session_made_on(hosts_app, "videodl.example")
    assert ask_with(hosts_app, "videodl.example", "/api/auth/me", token).status_code == 200
    assert ask_with(hosts_app, "videodl.example", "/api/auth/users", token).status_code == 200
    assert ask_with(hosts_app, "claude.example", "/api/auth/users", token).status_code == 401
    assert ask_with(hosts_app, "claude.example", "/api/auth/me", token).status_code == 401
    assert ask_with(hosts_app, "claude.example", "/api/secret", token).status_code == 401


def test_a_local_service_can_ask_who_a_session_belongs_to_and_nothing_more(hosts_app):
    token = session_made_on(hosts_app, "spotmp3.example")
    assert ask_with(hosts_app, LOCAL_CHECK_HOST, "/api/auth/me", token).json()["email"] == "drew@example.com"
    assert ask_with(hosts_app, LOCAL_CHECK_HOST, "/api/auth/users", token).status_code == 401
    assert ask_with(hosts_app, LOCAL_CHECK_HOST, "/api/secret", token).status_code == 401
    client = TestClient(hosts_app, base_url=f"https://{LOCAL_CHECK_HOST}")
    client.cookies.set(SESSION_COOKIE, token)
    assert client.post("/api/auth/logout").status_code in (401, 405)


def test_a_session_saved_before_hosts_were_recorded_must_sign_in_again(hosts_app, users):
    old = hosts_app.state.sessions.create(users.find_by_email("drew@example.com").id)
    assert ask_with(hosts_app, "claude.example", "/api/auth/users", old).status_code == 401
    assert ask_with(hosts_app, LOCAL_CHECK_HOST, "/api/auth/me", old).status_code == 200


def test_hosts_are_compared_without_case_or_default_ports():
    assert auth.normalise_host("Claude.Example:443") == "claude.example"
    assert auth.normalise_host("claude.example:80") == "claude.example"
    assert auth.normalise_host(" 127.0.0.1:8090 ") == "127.0.0.1:8090"
    assert auth.normalise_host("[::1]:443") == "[::1]"


@pytest.mark.parametrize("path", ["/assets/../app.js", "/assets/../index.html", "/vendor/fonts/../../workspace.js",
                                  "/assets/./../app.js"])
def test_public_prefixes_do_not_cover_paths_that_climb_out_of_them(path):
    assert not auth.is_public(path)


def test_a_climbing_path_to_app_code_still_needs_a_login(users, sessions):
    users.upsert("drew@example.com", "Drew", "admin", fast_hash(PASSWORD))
    app = build_app(users, sessions)

    @app.get("/{rest:path}")
    def anything(rest: str):
        return {"served": rest}

    response = TestClient(app).get("/assets/%2e%2e/app.js", follow_redirects=False)
    assert response.status_code == 303


def test_setup_stays_closed_when_the_accounts_file_goes_missing(setup_client, users):
    assert setup_client.post("/api/auth/setup", json=setup_body()).status_code == 200
    os.remove(users.path)
    stranger = TestClient(setup_client.app, base_url="https://testserver")
    page = stranger.get("/setup.html", follow_redirects=False)
    assert page.status_code == 503
    response = stranger.post("/api/auth/setup", json=setup_body(email="evil@example.com"))
    assert response.status_code == 503 and "users.json" in response.json()["detail"]
    assert not users.has_users()


def test_an_install_that_already_has_accounts_is_marked_as_set_up(users, sessions):
    users.upsert("drew@example.com", "Drew", "admin", fast_hash(PASSWORD))
    build_app(users, sessions)
    os.remove(users.path)
    assert users.was_set_up()
