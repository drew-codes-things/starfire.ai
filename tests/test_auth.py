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

    client.cookies.set(SESSION_COOKIE, sessions.create(user.id))
    assert client.get("/api/secret").json() == {"email": "drew@example.com"}


def test_forwarded_address_is_only_trusted_from_the_local_proxy():
    def request_from(peer, forwarded):
        scope = {"type": "http", "client": (peer, 1234), "headers": [(b"x-forwarded-for", forwarded.encode())]}
        return Request(scope)

    assert auth.client_address(request_from("127.0.0.1", "203.0.113.5, 10.0.0.1")) == "203.0.113.5"
    assert auth.client_address(request_from("192.168.1.50", "203.0.113.5")) == "192.168.1.50"


@pytest.fixture
def login_client(users, sessions, monkeypatch):
    import auth_routes
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(auth_routes, "limiter", LoginLimiter(max_failures=2, lockout_seconds=60))
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
    assert PASSWORD not in open(users.path).read()
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


@pytest.mark.parametrize("path", ["/vendor/marked.min.js", "/app.js", "/workspace.js", "/index.html", "/vendor/"])
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


def test_the_login_page_can_read_where_to_go_next():
    assert auth.is_public("/loginNext.js")


def test_logging_out_keeps_the_sessions_file_private(tmp_path):
    sessions = SessionStore(str(tmp_path))
    token = sessions.create("user-1")
    sessions.create("user-2")
    os.chmod(sessions.path, 0o644)
    sessions.revoke(token)
    assert stat.S_IMODE(os.stat(sessions.path).st_mode) == 0o600
