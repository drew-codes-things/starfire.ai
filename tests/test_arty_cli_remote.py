import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import arty_cli_remote
import auth
import test_arty_devices
from test_arty_devices import ARTY_KEY, BRAIN, client_for

setup = test_arty_devices.setup
brain = test_arty_devices.brain

SESSION = "0123456789abcdef"


@pytest.fixture
def remote(setup):
    setup["app"].include_router(arty_cli_remote.router)
    return setup


@pytest.fixture
def admin(remote):
    return client_for(remote, "admin")


def test_the_arty_cli_api_is_admin_only_even_to_look(remote, brain):
    member = client_for(remote, "member")
    for method, path in (("GET", "/api/arty-cli/sessions"), ("GET", f"/api/arty-cli/sessions/{SESSION}/events"),
                         ("POST", f"/api/arty-cli/sessions/{SESSION}/messages"),
                         ("POST", f"/api/arty-cli/sessions/{SESSION}/answers"),
                         ("POST", f"/api/arty-cli/sessions/{SESSION}/interrupt")):
        assert member.request(method, path, json={"text": "x", "request_id": "r", "allow": True}).status_code == 403, path
    assert client_for(remote, None).get("/api/arty-cli/sessions").status_code == 401
    assert auth.is_admin_only("GET", "/api/arty-cli/sessions")
    assert brain.calls == []


def test_sessions_are_listed_with_starfires_arty_key(admin, brain):
    brain.answer("GET", "/v1/remote/sessions", 200, {"sessions": [{"id": SESSION, "name": "VivaDrew"}]})
    assert admin.get("/api/arty-cli/sessions").json() == {"sessions": [{"id": SESSION, "name": "VivaDrew"}]}
    assert brain.calls[0]["auth"] == f"Bearer {ARTY_KEY}"


def test_events_are_read_after_a_point_with_a_capped_wait(admin, brain):
    brain.answer("GET", f"/v1/remote/sessions/{SESSION}/events", 200, {"events": [], "next": 4, "ended": False})
    assert admin.get(f"/api/arty-cli/sessions/{SESSION}/events", params={"after": 4, "wait": 999}).status_code == 200
    assert brain.calls[0]["url"] == f"{BRAIN}/remote/sessions/{SESSION}/events?after=4&wait={arty_cli_remote.MAX_WAIT_SECONDS}"


def test_a_message_an_answer_and_a_stop_reach_the_brain(admin, brain):
    for action in ("messages", "answers", "interrupt"):
        brain.answer("POST", f"/v1/remote/sessions/{SESSION}/{action}", 200, {"ok": True})
    assert admin.post(f"/api/arty-cli/sessions/{SESSION}/messages", json={"text": "run the tests"}).json() == {"ok": True}
    assert admin.post(f"/api/arty-cli/sessions/{SESSION}/answers", json={"request_id": "r1", "allow": False}).status_code == 200
    assert admin.post(f"/api/arty-cli/sessions/{SESSION}/interrupt").status_code == 200
    assert [call["body"] for call in brain.calls] == [{"text": "run the tests"}, {"request_id": "r1", "allow": False}, {}]


@pytest.mark.parametrize("session", ["../devices", "0123", "ZZZZZZZZZZZZZZZZ", "0123456789abcdef0"])
def test_a_bad_session_id_never_reaches_arty(admin, brain, session):
    assert admin.get(f"/api/arty-cli/sessions/{session}/events").status_code in (404, 422)
    assert brain.calls == []


def test_bad_bodies_never_reach_arty(admin, brain):
    assert admin.post(f"/api/arty-cli/sessions/{SESSION}/messages", json={"text": ""}).status_code == 422
    assert admin.post(f"/api/arty-cli/sessions/{SESSION}/messages", json={"text": "x" * 8001}).status_code == 422
    assert admin.post(f"/api/arty-cli/sessions/{SESSION}/answers", json={"request_id": "r1", "allow": "yes"}).status_code == 422
    assert brain.calls == []


@pytest.mark.parametrize("status", [404, 409, 410, 429])
def test_arty_saying_ended_or_already_answered_is_passed_on(admin, brain, status):
    brain.answer("POST", f"/v1/remote/sessions/{SESSION}/answers", status, {"error": {"message": "already answered by Phone"}})
    response = admin.post(f"/api/arty-cli/sessions/{SESSION}/answers", json={"request_id": "r1", "allow": True})
    assert response.status_code == status and "already answered" in response.json()["detail"]


def test_remote_control_is_switched_per_device(admin, brain):
    brain.answer("POST", "/v1/devices/3/remote-control", 200, {"ok": True})
    assert admin.post("/api/arty-devices/3/remote-control", json={"allow": True}).json() == {"ok": True}
    assert brain.calls[0]["body"] == {"allow": True}
    assert admin.post("/api/arty-devices/3/remote-control", json={"allow": "on"}).status_code == 422
