import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import arty_memory
import auth
import test_arty_devices
from test_arty_devices import ARTY_KEY, client_for

setup = test_arty_devices.setup
brain = test_arty_devices.brain

GRAPH = {
    "entities": [
        {"type": "entity", "name": "Rowan", "entityType": "person", "observations": ["Lives in Bath", "Likes tea"]},
        {"type": "entity", "name": "Garden", "entityType": "topic", "observations": ["Has a pond", 7, None]},
        {"type": "entity", "name": 12, "entityType": "topic", "observations": ["dropped"]},
    ],
    "relations": [{"from": "Rowan", "to": "Garden", "relationType": "tends"}],
}
EDITS = {
    "add": {"entity": "Rowan", "fact": "Plays chess", "type": "person"},
    "edit": {"entity": "Rowan", "old": "Lives in Bath", "new": "Lives in Bristol"},
    "delete": {"entity": "Rowan", "fact": "Likes tea"},
}


@pytest.fixture
def memory_app(setup):
    setup["app"].include_router(arty_memory.router)
    return setup


def test_an_admin_sees_arty_memory_as_names_types_and_facts(memory_app, brain):
    brain.answer("GET", "/v1/memory", 200, GRAPH)
    answer = client_for(memory_app, "admin").get("/api/arty-memory")
    assert answer.status_code == 200
    assert answer.json() == {"entities": [
        {"name": "Rowan", "type": "person", "facts": ["Lives in Bath", "Likes tea"]},
        {"name": "Garden", "type": "topic", "facts": ["Has a pond"]},
    ]}
    assert brain.calls[-1]["auth"] == f"Bearer {ARTY_KEY}" and brain.calls[-1]["url"].endswith("/v1/memory")


@pytest.mark.parametrize("action", EDITS)
def test_an_admin_edit_is_sent_to_the_brain_as_it_was_asked(memory_app, brain, action):
    brain.answer("POST", f"/v1/memory/{action}", 201 if action == "add" else 200, {"entity": "Rowan"})
    answer = client_for(memory_app, "admin").post(f"/api/arty-memory/{action}", json=EDITS[action])
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"entity": "Rowan"}
    assert brain.calls[-1]["body"] == EDITS[action] and brain.calls[-1]["method"] == "POST"


@pytest.mark.parametrize("status, message", [
    (400, "fact can be at most 300 characters"),
    (404, "nothing is saved about Nobody"),
    (409, "that fact is already saved under Rowan"),
    (503, "the memory store is down"),
])
def test_a_brain_refusal_is_shown_with_its_own_status_and_words(memory_app, brain, status, message):
    brain.answer("POST", "/v1/memory/add", status, {"error": {"message": message}})
    answer = client_for(memory_app, "admin").post("/api/arty-memory/add", json=EDITS["add"])
    assert answer.status_code == status and answer.json()["detail"] == message


def test_a_brain_that_is_down_or_refuses_the_key_is_reported(memory_app, brain):
    brain.answer("GET", "/v1/memory", 401, {"error": {"message": "bad key"}})
    assert client_for(memory_app, "admin").get("/api/arty-memory").status_code == 502
    brain.down = True
    answer = client_for(memory_app, "admin").get("/api/arty-memory")
    assert answer.status_code == 502 and "isn't answering" in answer.json()["detail"]


@pytest.mark.parametrize("method, path", [("GET", "/api/arty-memory"), *[("POST", f"/api/arty-memory/{a}") for a in EDITS]])
def test_an_account_that_is_not_an_admin_is_refused_and_the_brain_never_asked(memory_app, brain, method, path):
    member = client_for(memory_app, "member")
    answer = member.request(method, path, json=EDITS.get(path.rsplit("/", 1)[-1]))
    assert answer.status_code == 403
    signed_out = client_for(memory_app, None).request(method, path, json=EDITS.get(path.rsplit("/", 1)[-1]))
    assert signed_out.status_code == 401
    assert brain.calls == []


@pytest.mark.parametrize("body", [{}, {"entity": 5, "fact": "x"}, {"entity": "a", "fact": "x" * 20_001}])
def test_a_malformed_add_never_reaches_the_brain(memory_app, brain, body):
    answer = client_for(memory_app, "admin").post("/api/arty-memory/add", json=body)
    assert answer.status_code == 422 and brain.calls == []


def test_the_whole_app_refuses_ordinary_accounts_before_the_route():
    assert auth.is_admin_only("GET", "/api/arty-memory")
    assert auth.is_admin_only("POST", "/api/arty-memory/delete")


def static_text(name: str) -> str:
    with open(os.path.join(ROOT, "static", name), encoding="utf-8") as handle:
        return handle.read()


def test_the_memory_panel_sets_text_only_as_text():
    script = static_text("settingsArtyMemory.js")
    assert "textContent" in script
    for html_sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert html_sink not in script, html_sink


def test_the_memory_tab_and_panel_are_only_shown_to_admins():
    page = static_text("index.html")
    assert '<button class="settings-tab admin-only" type="button" data-tab="artymemory">' in page
    assert '<div class="settings-panel admin-only" data-panel="artymemory" hidden>' in page
    assert "body:not(.is-admin) .admin-only { display: none !important; }" in static_text("style.css")
