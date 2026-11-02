import os
import sqlite3
import subprocess
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


def test_everything_is_open_until_an_account_exists(users, sessions):
    client = TestClient(build_app(users, sessions))
    assert client.get("/index.html").status_code == 200


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


def test_import_copies_open_webui_accounts(tmp_path):
    database = tmp_path / "webui.db"
    connection = sqlite3.connect(database)
    connection.executescript("""
        create table user (id text, name text, email text, role text);
        create table auth (id text, email text, password text, active integer);
    """)
    rows = [("1", "Drew", "drew@example.com", "admin", 1), ("2", "Waiting", "w@example.com", "pending", 1),
            ("3", "Gone", "g@example.com", "user", 0)]
    for user_id, name, email, role, active in rows:
        connection.execute("insert into user values (?, ?, ?, ?)", (user_id, name, email, role))
        connection.execute("insert into auth values (?, ?, ?, ?)", (user_id, email, fast_hash(PASSWORD), active))
    connection.commit()
    connection.close()

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    result = subprocess.run([sys.executable, os.path.join(ROOT, "import_openwebui_users.py"), str(database)],
                            env={**os.environ, "DATA_DIR": str(data_dir)}, cwd=tmp_path,
                            capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "imported 1 account(s): Drew (admin)" in result.stdout
    assert "Waiting (role pending)" in result.stdout
    assert PASSWORD not in result.stdout and "$2b$" not in result.stdout
    imported = UserStore(str(data_dir))
    assert [u.email for u in imported.list()] == ["drew@example.com"]
    assert imported.verify("drew@example.com", PASSWORD)


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
    assert response.json() == {"email": "drew@example.com", "name": "Drew", "role": "admin"}
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
