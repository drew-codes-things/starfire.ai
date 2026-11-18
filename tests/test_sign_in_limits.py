import json
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
import routes
import upload_limits
import user_data
from auth import SessionStore, UserStore
from config import config

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}
MIB = 1024 * 1024
HOST_COOKIE = "__Host-starfire_session"
OLD_COOKIE = "starfire_session"


@pytest.fixture
def setup(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    users, sessions = UserStore(data_dir), SessionStore(data_dir)
    monkeypatch.setattr(config, "data_dir", data_dir)
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(auth_routes, "known_addresses", auth.KnownAddresses(data_dir))
    monkeypatch.setattr(routes, "registry", user_data.configure(data_dir))
    hashed = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    admin = users.upsert("admin@example.com", "Admin", "admin", hashed)
    app = FastAPI()
    upload_limits.install(app, config)
    auth.install(app, users, sessions)
    app.include_router(auth_routes.router)
    app.include_router(routes.router)
    return {"app": app, "sessions": sessions, "admin": admin}


def client(setup):
    return TestClient(setup["app"], base_url="https://testserver", headers=HEADERS)


def signed_in(setup):
    browser = client(setup)
    assert browser.post("/api/auth/login", json={"email": "admin@example.com", "password": PASSWORD}).status_code == 200
    return browser


def test_a_huge_anonymous_sign_in_body_is_refused_before_it_is_read(setup):
    junk = b'{"email": "a@example.com", "password": "' + b"x" * (4 * MIB) + b'"}'
    response = client(setup).post("/api/auth/login", content=junk, headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_a_chunked_sign_in_body_without_a_length_is_cut_off_too(setup):
    def body():
        yield b'{"email": "a@example.com", "password": "'
        for _ in range(4):
            yield b"x" * MIB

    response = client(setup).post("/api/auth/login", content=body(), headers={"Content-Type": "application/json"})
    assert response.status_code == 413


def test_every_other_request_body_has_a_cap_and_chat_imports_get_more_room(setup):
    browser = signed_in(setup)
    assert browser.post("/api/notes", content=b"x" * (9 * MIB), headers={"Content-Type": "application/json"}).status_code == 413
    assert browser.post("/api/chat_stream", content=b"x" * (33 * MIB),
                        headers={"Content-Type": "application/json"}).status_code == 413
    assert upload_limits.request_body_cap("/api/chat/sessions/import") > upload_limits.request_body_cap("/api/chat_stream")
    assert browser.post("/api/notes", json={"title": "still fine"}).status_code == 200


def test_one_address_gets_ten_sign_in_tries_a_minute_whatever_emails_it_uses(setup):
    browser = client(setup)
    for index in range(auth.LOGIN_ATTEMPTS_PER_ADDRESS):
        tried = browser.post("/api/auth/login", json={"email": f"nobody{index}@example.com", "password": "guess"})
        assert tried.status_code == 401
    refused = browser.post("/api/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    assert refused.status_code == 429 and int(refused.headers["Retry-After"]) > 0


def test_a_good_sign_in_clears_the_address_count(setup):
    browser = client(setup)
    for index in range(auth.LOGIN_ATTEMPTS_PER_ADDRESS - 1):
        browser.post("/api/auth/login", json={"email": f"nobody{index}@example.com", "password": "guess"})
    assert browser.post("/api/auth/login", json={"email": "admin@example.com", "password": PASSWORD}).status_code == 200
    assert browser.post("/api/auth/login", json={"email": "x@example.com", "password": "guess"}).status_code == 401


def test_guessing_the_current_password_on_the_change_form_is_limited(setup):
    browser = signed_in(setup)
    wrong = {"current": "not it", "password": "a brand new password", "confirm": "a brand new password"}
    for _ in range(auth.MAX_FAILED_LOGINS):
        assert browser.post("/api/auth/password", json=wrong).status_code == 400
    right = {**wrong, "current": PASSWORD}
    refused = browser.post("/api/auth/password", json=right)
    assert refused.status_code == 429 and int(refused.headers["Retry-After"]) > 0


def test_the_session_cookie_uses_the_host_prefix_and_drops_the_old_name(setup):
    response = client(setup).post("/api/auth/login", json={"email": "admin@example.com", "password": PASSWORD})
    cookies = response.headers.get_list("set-cookie")
    issued = next(cookie for cookie in cookies if cookie.startswith(HOST_COOKIE + "="))
    assert "secure" in issued.lower() and "path=/" in issued.lower() and "domain" not in issued.lower()
    assert any(cookie.startswith(OLD_COOKIE + '=""') or cookie.startswith(OLD_COOKIE + "=;") for cookie in cookies)


def issued_before_the_cookie_switch(sessions, token):
    stored = json.loads(open(sessions.path).read())
    stored[auth._token_digest(token)]["created"] = auth.LEGACY_COOKIE_ISSUED_UNTIL - 60
    with open(sessions.path, "w") as handle:
        json.dump(stored, handle)
    return token


def test_a_browser_still_holding_the_old_cookie_stays_signed_in_and_is_moved_to_the_new_one(setup):
    token = issued_before_the_cookie_switch(setup["sessions"], setup["sessions"].create(setup["admin"].id, "testserver"))
    browser = client(setup)
    browser.cookies.set(OLD_COOKIE, token)
    response = browser.get("/api/auth/me")
    assert response.status_code == 200
    assert any(cookie.startswith(f"{HOST_COOKIE}={token}") for cookie in response.headers.get_list("set-cookie"))


def test_an_old_name_cookie_the_server_never_issued_under_that_name_is_not_accepted(setup):
    token = setup["sessions"].create(setup["admin"].id, "testserver")
    browser = client(setup)
    browser.cookies.set(OLD_COOKIE, token)
    response = browser.get("/api/auth/me")
    assert response.status_code == 401
    assert not any(cookie.startswith(f"{HOST_COOKIE}={token}") for cookie in response.headers.get_list("set-cookie"))


def test_changing_the_password_with_only_the_old_cookie_keeps_the_fresh_session(setup):
    old = issued_before_the_cookie_switch(setup["sessions"], setup["sessions"].create(setup["admin"].id, "testserver"))
    browser = client(setup)
    browser.cookies.set(OLD_COOKIE, old)
    changed = browser.post("/api/auth/password", json={"current": PASSWORD, "password": "a brand new password",
                                                      "confirm": "a brand new password"})
    assert changed.status_code == 200
    issued = [cookie for cookie in changed.headers.get_list("set-cookie") if cookie.startswith(HOST_COOKIE + "=")]
    assert len(issued) == 1 and not issued[0].startswith(f"{HOST_COOKIE}={old}")
    fresh = issued[0].split(";", 1)[0].split("=", 1)[1]
    assert setup["sessions"].user_id_for(fresh) == setup["admin"].id


def test_signing_out_ends_the_sessions_under_both_cookie_names(setup):
    sessions = setup["sessions"]
    new = sessions.create(setup["admin"].id, "testserver")
    old = issued_before_the_cookie_switch(sessions, sessions.create(setup["admin"].id, "testserver"))
    browser = client(setup)
    browser.cookies.set(HOST_COOKIE, new)
    browser.cookies.set(OLD_COOKIE, old)
    assert browser.post("/api/auth/logout").status_code == 200
    assert sessions.user_id_for(new) is None and sessions.user_id_for(old) is None
