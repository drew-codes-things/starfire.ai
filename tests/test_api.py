import os
import sys
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="starfire-test-"))

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import model_endpoints
import routes
import shell_tool
from chat_session_store import ChatSessionStore
from folder_store import FolderStore
from mcp_servers_store import McpServerStore
from memory_store import MemoryStore
from note_store import NoteStore, NoteTemplateStore
from prompt_store import PromptStore
from task_store import TaskRunStore, TaskStore
from version import VERSION


@pytest.fixture
def client(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    monkeypatch.setattr(routes, "chat_sessions", ChatSessionStore(data_dir))
    monkeypatch.setattr(routes, "chat_folders", FolderStore(data_dir))
    monkeypatch.setattr(routes, "notes", NoteStore(data_dir))
    monkeypatch.setattr(routes, "note_templates", NoteTemplateStore(data_dir))
    monkeypatch.setattr(routes, "tasks", TaskStore(data_dir))
    monkeypatch.setattr(routes, "task_runs", TaskRunStore(data_dir))
    monkeypatch.setattr(routes, "prompts", PromptStore(data_dir))
    monkeypatch.setattr(routes, "memory", MemoryStore(data_dir))
    monkeypatch.setattr(routes, "mcp_servers", McpServerStore(data_dir))
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_chat_sessions_round_trip(client):
    created = client.post("/api/chat/sessions", json={"title": "First chat"}).json()
    assert created["title"] == "First chat"
    session_id = created["id"]

    messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello", "model": "m"}]
    assert client.put(f"/api/chat/sessions/{session_id}",
                      json={"messages": messages, "tags": ["Demo"], "pinned": True}).status_code == 200
    fetched = client.get(f"/api/chat/sessions/{session_id}").json()
    assert fetched["messages"] == messages
    assert fetched["pinned"] is True

    listing = client.get("/api/chat/sessions").json()["sessions"]
    assert [s["id"] for s in listing] == [session_id]
    assert client.get("/api/chat/sessions", params={"q": "tag:demo"}).json()["sessions"]
    assert client.get("/api/chat/sessions", params={"q": "nothing-like-this"}).json()["sessions"] == []

    clone = client.post(f"/api/chat/sessions/{session_id}/clone").json()
    assert clone["id"] != session_id
    assert client.get(f"/api/chat/sessions/{clone['id']}").json()["messages"] == messages

    assert client.post("/api/chat/sessions/archive-all").json()["archived"] == 2
    assert client.get("/api/chat/sessions").json()["sessions"] == []
    assert len(client.get("/api/chat/sessions", params={"archived": "true"}).json()["sessions"]) == 2

    assert client.delete(f"/api/chat/sessions/{clone['id']}").status_code == 200
    assert client.get(f"/api/chat/sessions/{clone['id']}").status_code == 404
    assert client.delete("/api/chat/sessions").status_code == 400
    assert client.delete("/api/chat/sessions", params={"confirm": "delete-all"}).json()["deleted"] == 1


def test_import_accepts_starfire_and_open_webui_exports(client):
    export = {"chats": [
        {"title": "Mine", "messages": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
         "tags": ["one"], "created": "2026-01-02T03:04:05Z"},
        {"title": "Open WebUI", "chat": {"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "r"}]},
         "created_at": 1700000000},
        {"title": "Empty", "chat": {"messages": []}},
        {"no": "messages at all"},
    ]}
    result = client.post("/api/chat/sessions/import", json=export).json()
    assert result == {"imported": 2, "skipped": 2}
    titles = sorted(s["title"] for s in client.get("/api/chat/sessions").json()["sessions"])
    assert titles == ["Mine", "Open WebUI"]
    assert client.post("/api/chat/sessions/import", json={"chats": [{"chat": {"messages": []}}]}).status_code == 400


def test_folders_hold_chats(client):
    folder = client.post("/api/folders", json={"name": "Work"}).json()
    session = client.post("/api/chat/sessions", json={"title": "In a folder"}).json()
    client.put(f"/api/chat/sessions/{session['id']}", json={"folder_id": folder["id"]})
    assert client.get(f"/api/chat/sessions/{session['id']}").json()["folder_id"] == folder["id"]
    assert client.put(f"/api/folders/{folder['id']}", json={"name": "Play"}).status_code == 200
    assert client.get("/api/folders").json()["folders"][0]["name"] == "Play"
    assert client.delete(f"/api/folders/{folder['id']}").status_code == 200
    assert client.get(f"/api/chat/sessions/{session['id']}").json()["folder_id"] == ""


def test_notes_checklists_and_natural_dates(client):
    note = client.post("/api/notes", json={
        "title": "Groceries", "note_type": "checklist", "due_date": "tomorrow at 9am",
        "items": [{"text": "milk", "done": False}, {"text": "eggs", "done": False}],
    }).json()
    assert "T09:00" in note["due_date"]
    assert client.post(f"/api/notes/{note['id']}/items/1/toggle").json() == {"ok": True}
    assert client.post(f"/api/notes/{note['id']}/items/9/toggle").status_code == 404
    assert client.post(f"/api/notes/{note['id']}/pin", json={"pinned": True}).json() == {"ok": True}
    assert client.put(f"/api/notes/{note['id']}", json={"title": "Shopping"}).status_code == 200
    fetched = client.get(f"/api/notes/{note['id']}").json()
    assert fetched["title"] == "Shopping" and fetched["pinned"] is True
    assert fetched["items"][1]["done"] is True and fetched["items"][0]["done"] is False
    assert client.post(f"/api/notes/{note['id']}/archive", json={"archived": True}).status_code == 200
    assert client.get("/api/notes", params={"archived": "false"}).json()["notes"] == []
    assert len(client.get("/api/notes", params={"archived": "true"}).json()["notes"]) == 1
    assert client.delete(f"/api/notes/{note['id']}").status_code == 200
    assert client.get(f"/api/notes/{note['id']}").status_code == 404


def test_tasks_pause_and_resume(client):
    task = client.post("/api/tasks", json={
        "name": "Morning", "prompt": "Summarise my notes", "schedule": "daily", "scheduled_time": "08:00",
        "enabled_builtin_tools": ["manage_notes"],
    }).json()
    assert task["id"] and task["next_run"]
    assert client.post(f"/api/tasks/{task['id']}/pause").json() == {"ok": True}
    assert client.get("/api/tasks").json()["tasks"][0]["status"] == "paused"
    assert client.post(f"/api/tasks/{task['id']}/resume").json() == {"ok": True}
    assert client.get("/api/tasks").json()["tasks"][0]["status"] != "paused"
    assert client.post("/api/tasks/nope/pause").status_code == 404
    assert client.post("/api/tasks", json={"name": "bad", "prompt": "x", "schedule": "cron",
                                           "cron_expression": "not a cron"}).status_code == 400
    assert client.get("/api/tasks").json()["tasks"][0]["name"] == "Morning"
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 200
    assert client.get("/api/tasks").json()["tasks"] == []


def test_prompts_and_memory(client):
    prompt = client.post("/api/prompts", json={"command": "/Summarize", "title": "Sum", "content": "Summarise:"}).json()
    assert prompt["command"] == "summarize"
    assert client.post("/api/prompts", json={"command": "summarize", "content": "again"}).status_code == 400
    assert client.put(f"/api/prompts/{prompt['id']}", json={"command": "sum", "content": "Shorter"}).json() == {"ok": True}
    assert client.get("/api/prompts").json()["prompts"][0]["command"] == "sum"
    assert client.delete(f"/api/prompts/{prompt['id']}").status_code == 200

    fact = client.post("/api/memory", json={"text": "user prefers tea", "category": "preference"}).json()
    assert fact["category"] == "preference"
    assert client.post("/api/memory", json={"text": "   "}).status_code == 400
    assert [m["text"] for m in client.get("/api/memory").json()["memories"]] == ["user prefers tea"]
    assert client.delete(f"/api/memory/{fact['id']}").status_code == 200


def test_config_reports_version_and_model_patterns(client):
    data = client.get("/api/config").json()
    assert data["version"] == VERSION
    assert data["own_tools_models"] == ["Arty", "assistant-core", "arty/*"]
    assert data["inline_media_prefixes"] == ["/arty-files/"]


def model_server_replies(monkeypatch, replies):
    asked = []

    def handle(request):
        address = f"{request.url.host}:{request.url.port}{request.url.path}"
        asked.append(address)
        reply = replies.get(address)
        if reply is None:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, headers=reply[0], text=reply[1])

    real_client = httpx.AsyncClient
    monkeypatch.setattr(model_endpoints.httpx, "AsyncClient",
                        lambda *args, **kwargs: real_client(*args, transport=httpx.MockTransport(handle), **kwargs))
    return asked


ENGINE_STAND_IN = {"127.0.0.1:11438/api/tags": ([("Server", "arty-engine/9.9")], "{}"),
                   "127.0.0.1:11438/api/pull": ([], '{"status": "success"}')}


def stored_endpoints(monkeypatch, tmp_path, *base_urls):
    store = model_endpoints.ModelEndpointStore(str(tmp_path))
    for base_url in base_urls:
        store.add(base_url, "ollama", label="Ollama (auto-detected)")
    monkeypatch.setattr(routes, "endpoints", store)
    return store


def test_backend_status_names_an_arty_engine_that_runs_without_ollama_installed(client, monkeypatch):
    monkeypatch.setattr(routes.config, "ollama_base_url", "http://engine.test:11435")
    monkeypatch.setattr(routes.ollama_manager, "is_installed", lambda: False)
    model_server_replies(monkeypatch, {
        "engine.test:11435/api/tags": ([("Server", "Caddy"), ("Server", "arty-engine/0.1.0 Python/3.13.5")], "{}"),
    })
    status = client.get("/api/hardware/ollama").json()
    assert (status["installed"], status["running"], status["backend"]) == (False, True, "ArtyEngine")
    assert (status["in_use"], status["found_at"]) == (True, None)


def test_backend_status_names_a_running_ollama_that_is_not_added_yet(client, monkeypatch, tmp_path):
    monkeypatch.setattr(routes.config, "ollama_base_url", None)
    monkeypatch.setattr(routes.ollama_manager, "is_installed", lambda: True)
    stored_endpoints(monkeypatch, tmp_path)
    model_server_replies(monkeypatch, {
        "127.0.0.1:11434/api/tags": ([], "{}"),
        "127.0.0.1:11434/": ([], "Ollama is running"),
    })
    status = client.get("/api/hardware/ollama").json()
    assert status == {"installed": True, "running": True, "backend": "Ollama", "install": None,
                      "in_use": False, "found_at": "http://127.0.0.1:11434"}
    assert routes.endpoints.list() == []


def test_backend_status_reports_the_stored_backend_and_ignores_another_on_the_engine_port(client, monkeypatch, tmp_path):
    monkeypatch.setattr(routes.config, "ollama_base_url", None)
    monkeypatch.setattr(routes.ollama_manager, "is_installed", lambda: True)
    stored_endpoints(monkeypatch, tmp_path, "http://127.0.0.1:11434")
    asked = model_server_replies(monkeypatch, {"127.0.0.1:11434/api/tags": ([], "{}"), **ENGINE_STAND_IN})
    status = client.get("/api/hardware/ollama").json()
    assert (status["running"], status["backend"], status["in_use"], status["found_at"]) == (True, "Ollama", True, None)
    assert not [address for address in asked if ":11438" in address]


def test_config_names_the_stored_backend_without_probing(client, monkeypatch, tmp_path):
    monkeypatch.setattr(routes.config, "ollama_base_url", None)
    stored_endpoints(monkeypatch, tmp_path, "http://localhost:11434")
    asked = model_server_replies(monkeypatch, ENGINE_STAND_IN)
    assert client.get("/api/config").json()["ollama_base_url"] == "http://localhost:11434"
    assert asked == []


def test_config_offers_the_default_address_when_no_backend_is_stored(client, monkeypatch, tmp_path):
    monkeypatch.setattr(routes.config, "ollama_base_url", None)
    stored_endpoints(monkeypatch, tmp_path)
    asked = model_server_replies(monkeypatch, ENGINE_STAND_IN)
    assert client.get("/api/config").json()["ollama_base_url"] == "http://localhost:11434"
    assert asked == []


def test_a_pull_is_refused_rather_than_sent_to_a_backend_nobody_chose(client, monkeypatch, tmp_path):
    monkeypatch.setattr(routes.config, "ollama_base_url", None)
    stored_endpoints(monkeypatch, tmp_path)
    asked = model_server_replies(monkeypatch, ENGINE_STAND_IN)
    response = client.post("/api/hardware/pull", json={"model": "nomic-embed-text"})
    assert response.status_code == 502
    assert "Settings" in response.json()["detail"]
    assert asked == []


def test_a_pull_goes_to_the_stored_backend(client, monkeypatch, tmp_path):
    monkeypatch.setattr(routes.config, "ollama_base_url", None)
    stored_endpoints(monkeypatch, tmp_path, "http://127.0.0.1:11434")
    asked = model_server_replies(monkeypatch, {"127.0.0.1:11434/api/pull": ([], '{"status": "success"}'),
                                               **ENGINE_STAND_IN})
    response = client.post("/api/hardware/pull", json={"model": "nomic-embed-text"})
    assert response.status_code == 200
    assert "success" in response.text
    assert asked == ["127.0.0.1:11434/api/pull"]


def test_backend_status_reports_nothing_running_when_the_configured_server_is_down(client, monkeypatch):
    monkeypatch.setattr(routes.config, "ollama_base_url", "http://engine.test:11435")
    monkeypatch.setattr(routes.ollama_manager, "is_installed", lambda: False)
    model_server_replies(monkeypatch, {})
    status = client.get("/api/hardware/ollama").json()
    assert (status["installed"], status["running"], status["backend"]) == (False, False, None)
    assert status["install"]


def test_config_lists_only_the_patterns_of_models_with_a_server_voice(client, monkeypatch):
    monkeypatch.setattr(routes.config, "model_voices", "side/*=secret-voice.onnx@1.1")
    data = client.get("/api/config").json()
    assert data["voice_models"] == ["side/*"]
    assert "secret-voice" not in str(data)


def test_a_broken_voice_setting_is_logged_and_leaves_voices_off(client, monkeypatch, caplog):
    monkeypatch.setattr(routes.config, "model_voices", "side/*=../escape.onnx")
    assert client.get("/api/config").json()["voice_models"] == []
    assert client.post("/api/speak", json={"model": "side/one", "text": "Hi."}).status_code == 404
    assert "MODEL_VOICES is ignored" in caplog.text


def test_speak_gives_wav_audio_in_the_model_voice(client, monkeypatch):
    spoken = []

    async def fake_speak(text, rule, voices_dir):
        spoken.append((text, rule.voice_file, rule.speed, voices_dir))
        return b"RIFF-audio"
    monkeypatch.setattr(routes.config, "model_voices", "side/*=v.onnx@1.1")
    monkeypatch.setattr(routes.config, "model_voices_dir", "/voices")
    monkeypatch.setattr(routes.model_voices, "speak", fake_speak)
    response = client.post("/api/speak", json={"model": "side/one", "text": "Hello there."})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == b"RIFF-audio"
    assert spoken == [("Hello there.", "v.onnx", 1.1, "/voices")]


def test_speak_refuses_models_without_a_voice_and_bad_text(client, monkeypatch):
    monkeypatch.setattr(routes.config, "model_voices", "side/*=v.onnx")
    assert client.post("/api/speak", json={"model": "qwen3:14b", "text": "Hi."}).status_code == 404
    assert client.post("/api/speak", json={"model": "side/one", "text": "   "}).status_code == 400
    assert client.post("/api/speak", json={"model": "side/one", "text": "a" * 601}).status_code == 400


def test_speak_reports_a_missing_voice_file(client, monkeypatch, tmp_path):
    monkeypatch.setattr(routes.config, "model_voices", "side/*=gone.onnx")
    monkeypatch.setattr(routes.config, "model_voices_dir", str(tmp_path))
    response = client.post("/api/speak", json={"model": "side/one", "text": "Hi."})
    assert response.status_code == 503
    assert "gone.onnx" in response.json()["detail"]


def test_warming_a_brain_model_asks_the_brain_to_load_it(client, monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, timeout):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None, headers=None):
            calls.append((url, json, headers))
            return FakeResponse()

    class FakeResponse:
        async def aclose(self):
            pass

    class Endpoint:
        id, provider, base_url = "brain", "openai", "http://brain:8081/v1/"
    monkeypatch.setattr(routes.endpoints, "get", lambda endpoint_id: Endpoint() if endpoint_id == "brain" else None)
    monkeypatch.setattr(routes, "_api_key_for", lambda endpoint_id, provider: "key-1")
    monkeypatch.setattr(routes.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(routes.config, "own_tools_models", "Arty,side/*")
    assert client.post("/api/warm", json={"endpoint_id": "brain", "model": "side/one"}).json() == {"ok": True}
    assert client.post("/api/warm", json={"endpoint_id": "brain", "model": "gpt-4o"}).json() == {"ok": True}
    assert calls == [("http://brain:8081/v1/warm", {"model": "side/one"}, {"Authorization": "Bearer key-1"})]


class FakeOllamaClient:
    calls = []

    def __init__(self, timeout):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        FakeOllamaClient.calls.append((url, json))
        return FakeOllamaResponse()


class FakeOllamaResponse:
    async def aclose(self):
        pass


def test_warming_an_ollama_model_asks_for_the_brains_window_without_context_shift(client, monkeypatch):
    class Endpoint:
        id, provider, base_url = "local", "ollama", "http://ollama:11435"
    FakeOllamaClient.calls = []
    monkeypatch.setattr(routes.endpoints, "get", lambda endpoint_id: Endpoint() if endpoint_id == "local" else None)
    monkeypatch.setattr(routes.httpx, "AsyncClient", FakeOllamaClient)
    monkeypatch.setattr(routes.config, "task_num_ctx", 24576)
    assert client.post("/api/warm", json={"endpoint_id": "local", "model": "qwen3:14b"}).json() == {"ok": True}
    assert FakeOllamaClient.calls == [("http://ollama:11435/api/generate",
                                       {"model": "qwen3:14b", "keep_alive": -1,
                                        "options": {"num_ctx": 24576}, "shift": False})]


def test_a_helper_task_asks_for_the_brains_window_without_context_shift(client, monkeypatch):
    from types import SimpleNamespace

    import chat_stream

    sent = {}

    async def fake_iter(http_client, url, headers, payload):
        sent.update(url=url, payload=payload)
        yield ("delta", "Plex setup")

    class Endpoint:
        id, provider, base_url = "local", "ollama", "http://127.0.0.1:11434"
    monkeypatch.setattr(routes.endpoints, "list", lambda: [Endpoint()])
    monkeypatch.setattr(routes, "_api_key_for", lambda endpoint_id, provider: None)
    monkeypatch.setattr(routes, "usage", SimpleNamespace(record=lambda *args: None))
    monkeypatch.setattr(routes.config, "task_model", "qwen3:14b")
    monkeypatch.setattr(routes.config, "task_num_ctx", 24576)
    monkeypatch.setattr(chat_stream, "_iter_ollama", fake_iter)
    reply = client.post("/api/quick-complete", json={"endpoint_id": "brain", "model": "Arty",
                                                      "prompt": "title this", "helper": True})
    assert reply.json() == {"text": "Plex setup"}
    assert sent["url"] == "http://127.0.0.1:11434/api/chat"
    assert sent["payload"]["model"] == "qwen3:14b"
    assert sent["payload"]["options"]["num_ctx"] == 24576
    assert sent["payload"]["shift"] is False


def test_code_check_route(client):
    ok = client.post("/api/code-check", json={"language": "python", "code": "x = 1\n"}).json()
    assert ok["status"] == "ok"
    bad = client.post("/api/code-check", json={"language": "json", "code": "{"}).json()
    assert bad["status"] == "error"


def test_pending_shell_commands_can_be_approved_or_rejected(client, monkeypatch):
    monkeypatch.setattr(routes, "_current_user_is_active_admin", lambda: True)
    assert client.get("/api/pending-shell").json()["commands"] == []
    assert client.post("/api/pending-shell/nope/approve").status_code == 404

    async def stage_and_wait():
        pending_id = shell_tool.stage("echo hi", None, owner=None)
        assert client.get("/api/pending-shell").json()["commands"][0]["command"] == "echo hi"
        assert client.post(f"/api/pending-shell/{pending_id}/reject").json() == {"rejected": True}
        assert await shell_tool.wait_for_decision(pending_id) is False
        assert client.post(f"/api/pending-shell/{pending_id}/approve").status_code == 404

    import asyncio
    asyncio.run(stage_and_wait())


def test_mcp_server_add_validates_remote_urls(client, monkeypatch):
    async def fake_connect(server, auth=None):
        return [{"name": "ping", "description": "", "input_schema": {}}]

    monkeypatch.setattr(routes.mcp_manager, "connect_server", fake_connect)
    monkeypatch.setattr(routes, "_current_user_is_active_admin", lambda: True)
    bad = client.post("/api/mcp/servers", json={"transport": "http", "url": "localhost:3001"})
    assert bad.status_code == 400
    assert client.post("/api/mcp/servers", json={"transport": "carrier-pigeon", "url": "http://x"}).status_code == 400
    good = client.post("/api/mcp/servers", json={"transport": "http", "url": "http://localhost:3001/mcp",
                                                  "headers": {"Authorization": "Bearer t"}}).json()
    assert good["tool_count"] == 1
    listed = client.get("/api/mcp/servers").json()["servers"][0]
    assert listed["transport"] == "http" and listed["url"] == "http://localhost:3001/mcp"
    assert "headers" not in listed


def test_chat_controls_are_saved_with_the_chat(client):
    session_id = client.post("/api/chat/sessions", json={"title": "Tuned chat"}).json()["id"]
    untouched = client.get(f"/api/chat/sessions/{session_id}").json()
    assert untouched["system_prompt"] is None and untouched["params"] is None

    controls = {"system_prompt": "Answer in one line.", "params": {"temperature": 0.2, "num_ctx": 4096}}
    assert client.put(f"/api/chat/sessions/{session_id}", json=controls).status_code == 200
    saved = client.get(f"/api/chat/sessions/{session_id}").json()
    assert saved["system_prompt"] == "Answer in one line."
    assert saved["params"] == {"temperature": 0.2, "num_ctx": 4096}

    assert client.put(f"/api/chat/sessions/{session_id}", json={"title": "Renamed"}).status_code == 200
    assert client.get(f"/api/chat/sessions/{session_id}").json()["system_prompt"] == "Answer in one line."

    clone = client.post(f"/api/chat/sessions/{session_id}/clone").json()
    assert client.get(f"/api/chat/sessions/{clone['id']}").json()["params"] == {"temperature": 0.2, "num_ctx": 4096}

    cleared = {"system_prompt": "", "params": {}}
    assert client.put(f"/api/chat/sessions/{session_id}", json=cleared).status_code == 200
    emptied = client.get(f"/api/chat/sessions/{session_id}").json()
    assert emptied["system_prompt"] == "" and emptied["params"] == {}


@pytest.mark.parametrize("controls", [
    {"params": {"temperature": "hot"}},
    {"params": ["temperature", 1]},
    {"params": {"x" * 80: 1}},
    {"params": {f"key{i}": i for i in range(40)}},
    {"system_prompt": "x" * 40000},
])
def test_chat_controls_reject_bad_values(client, controls):
    session_id = client.post("/api/chat/sessions", json={"title": "Strict chat"}).json()["id"]
    assert client.put(f"/api/chat/sessions/{session_id}", json=controls).status_code == 422
    assert client.get(f"/api/chat/sessions/{session_id}").json()["params"] is None


def test_exported_chats_keep_their_controls_when_imported(client):
    export = [{"title": "Tuned", "system_prompt": "Be brief.", "params": {"top_p": 0.5, "bad": "text"},
               "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]}]
    assert client.post("/api/chat/sessions/import", json={"chats": export}).json()["imported"] == 1
    session_id = client.get("/api/chat/sessions").json()["sessions"][0]["id"]
    imported = client.get(f"/api/chat/sessions/{session_id}").json()
    assert imported["system_prompt"] == "Be brief." and imported["params"] == {"top_p": 0.5}


def test_pending_approvals_are_listed_for_their_owner_only(client):
    import file_edit_tool
    import user_data

    edit_id = file_edit_tool.stage("/tmp/demo.txt", "new", "--- a\n+++ b\n", user_data.current_user_id.get())
    other_id = file_edit_tool.stage("/tmp/other.txt", "new", "diff", "someone-else")
    try:
        listed = client.get("/api/pending-edits").json()["edits"]
        assert [edit["id"] for edit in listed] == [edit_id]
        assert listed[0]["path"] == "/tmp/demo.txt" and listed[0]["diff"].startswith("--- a")
        assert client.get("/api/pending-shell").json() == {"commands": []}
    finally:
        file_edit_tool.remove_pending(edit_id, user_data.current_user_id.get())
        file_edit_tool.remove_pending(other_id, "someone-else")


def test_a_task_the_model_creates_in_a_chat_runs_on_that_chat_endpoint(client, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import agent_loop

    endpoint = SimpleNamespace(id="ep1", base_url="http://model", provider="openai", label="Model", kind="api")
    monkeypatch.setattr(routes, "endpoints", SimpleNamespace(get=lambda endpoint_id: endpoint if endpoint_id == "ep1" else None))
    monkeypatch.setattr(routes, "api_keys", SimpleNamespace(load=lambda: {}))
    monkeypatch.setattr(routes, "usage", SimpleNamespace(record=lambda *args: None))
    create_call = {"id": "c1", "name": "manage_tasks", "arguments": {
        "action": "create", "prompt": "Summarise my notes", "schedule": "daily", "scheduled_time": "09:00"}}
    replies = iter([("tool_calls", [create_call]), ("delta", "Task created."), ("delta", "Here is the summary.")])

    async def fake_stream(*args, **kwargs):
        yield next(replies)

    monkeypatch.setattr(agent_loop, "stream_chat_once", fake_stream)
    response = client.post("/api/chat_stream", json={
        "endpoint_id": "ep1", "model": "m", "enabled_builtin_tools": ["manage_tasks"],
        "messages": [{"role": "user", "content": "every morning at 9, summarise my notes"}]})
    assert response.status_code == 200
    [task] = routes.tasks.list()
    assert task.endpoint_id == "ep1"
    run = routes._task_chat_fn(task.endpoint_id, task.model, task.prompt, task.enabled_mcp_servers,
                               task.enabled_builtin_tools)
    assert asyncio.run(run) == "Here is the summary."


@pytest.fixture
def model_inbox(client, monkeypatch):
    from types import SimpleNamespace

    endpoint = SimpleNamespace(id="ep1", base_url="http://model", provider="openai", label="Model", kind="api")
    monkeypatch.setattr(routes, "endpoints", SimpleNamespace(get=lambda endpoint_id: endpoint if endpoint_id == "ep1" else None))
    monkeypatch.setattr(routes, "api_keys", SimpleNamespace(load=lambda: {}))
    monkeypatch.setattr(routes, "usage", SimpleNamespace(record=lambda *args: None))
    received = []

    async def fake_stream(base_url, api_key, model, messages, *args, **kwargs):
        received.append(messages[-1]["content"])
        yield b'data: {"delta": "ok"}\n\n'

    monkeypatch.setattr(routes, "stream_chat", fake_stream)
    return received


def send(client, text, **extra):
    return client.post("/api/chat_stream", json={
        "endpoint_id": "ep1", "model": "m", "messages": [{"role": "user", "content": text}], **extra})


@pytest.mark.parametrize("text", [
    "Save the file as CSV instead of JSON",
    "Note that the function returns null. Why?",
    "remember when we fixed the login bug? do that again",
    "Store manager wants a refund letter, write one",
    "Please remember: my dog is Rex",
    "remember - the meeting moved",
    "memorize: the capital of France",
])
def test_ordinary_messages_reach_the_model_and_are_not_saved(client, model_inbox, text):
    response = send(client, text, memory_commands=True)
    assert response.status_code == 200
    assert model_inbox == [text]
    assert routes.memory.list() == []


def test_remember_with_a_colon_is_saved_without_calling_the_model(client, model_inbox):
    response = send(client, "Remember:  my dog is Rex ", memory_commands=True)
    assert "Saved to memory" in response.text
    assert model_inbox == []
    assert [entry.text for entry in routes.memory.list()] == ["my dog is Rex"]


@pytest.mark.parametrize("extra", [{}, {"memory_commands": False}])
def test_temporary_side_and_other_chats_never_save_a_memory(client, model_inbox, extra):
    response = send(client, "remember: my dog is Rex", **extra)
    assert response.status_code == 200
    assert model_inbox == ["remember: my dog is Rex"]
    assert routes.memory.list() == []


@pytest.mark.parametrize("content", [[{"type": "text", "text": "hi"}], {"text": "hi"}, 42])
def test_a_chat_message_whose_content_is_not_text_is_refused_not_a_crash(client, model_inbox, content):
    response = client.post("/api/chat_stream", json={
        "endpoint_id": "ep1", "model": "m", "messages": [{"role": "user", "content": content}]})
    assert response.status_code == 422
    assert model_inbox == []


def test_a_pull_goes_to_the_server_root_of_an_endpoint_stored_with_an_api_path(client, monkeypatch, tmp_path):
    monkeypatch.setattr(routes.config, "ollama_base_url", None)
    stored_endpoints(monkeypatch, tmp_path, "http://127.0.0.1:11434/api")
    asked = model_server_replies(monkeypatch, {"127.0.0.1:11434/api/pull": ([], '{"status": "success"}')})
    response = client.post("/api/hardware/pull", json={"model": "nomic-embed-text"})
    assert "success" in response.text
    assert asked == ["127.0.0.1:11434/api/pull"]


def ollama_started_by_the_button(monkeypatch):
    async def start(_override):
        return True, "Started."

    monkeypatch.setattr(routes.ollama_manager, "start", start)


def test_starting_ollama_from_the_hardware_tab_adds_it_when_no_backend_is_set_up(client, monkeypatch, tmp_path):
    monkeypatch.setattr(routes.config, "ollama_base_url", None)
    stored_endpoints(monkeypatch, tmp_path)
    ollama_started_by_the_button(monkeypatch)
    model_server_replies(monkeypatch, {"127.0.0.1:11434/api/tags": ([], "{}"), **ENGINE_STAND_IN})
    response = client.post("/api/hardware/ollama/start").json()
    assert response["added"] == "Ollama (auto-detected)"
    assert [(e.base_url, e.label) for e in routes.endpoints.list()] == [("http://127.0.0.1:11434", "Ollama (auto-detected)")]
    status = client.get("/api/hardware/ollama").json()
    assert (status["in_use"], status["found_at"]) == (True, None)


def test_starting_ollama_adds_nothing_when_a_local_backend_is_already_set_up(client, monkeypatch, tmp_path):
    monkeypatch.setattr(routes.config, "ollama_base_url", None)
    stored_endpoints(monkeypatch, tmp_path, "http://127.0.0.1:11438")
    ollama_started_by_the_button(monkeypatch)
    model_server_replies(monkeypatch, {"127.0.0.1:11434/api/tags": ([], "{}"), **ENGINE_STAND_IN})
    assert client.post("/api/hardware/ollama/start").json()["added"] is None
    assert [e.base_url for e in routes.endpoints.list()] == ["http://127.0.0.1:11438"]


def model_server_statuses(monkeypatch, replies):
    asked = []

    def handle(request):
        address = f"{request.url.host}:{request.url.port}{request.url.path}"
        asked.append(address)
        reply = replies.get(address)
        if reply is None:
            raise httpx.ConnectError("connection refused", request=request)
        status, text = reply
        return httpx.Response(status, text=text)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(model_endpoints.httpx, "AsyncClient",
                        lambda *args, **kwargs: real_client(*args, transport=httpx.MockTransport(handle), **kwargs))
    return asked


OPENAI_ONLY_SERVER = {"127.0.0.1:1234/api/tags": (404, "not found"), "127.0.0.1:1234/v1/models": (200, '{"data": []}'),
                      "localhost:1234/api/tags": (404, "not found"), "localhost:1234/v1/models": (200, '{"data": []}')}
OLLAMA_SERVER = {"localhost:11434/api/tags": (200, '{"models": []}'), "localhost:11434/v1/models": (200, '{"data": []}')}


@pytest.mark.parametrize("base_url, kind", [("http://127.0.0.1:1234", "openai-compatible"),
                                            ("http://127.0.0.1:1234", "ollama"),
                                            ("http://localhost:1234/v1", "ollama")])
def test_a_local_server_without_the_ollama_api_is_added_as_openai_compatible(client, monkeypatch, tmp_path, base_url, kind):
    store = stored_endpoints(monkeypatch, tmp_path)
    model_server_statuses(monkeypatch, OPENAI_ONLY_SERVER)
    response = client.post("/api/model-endpoints", json={"base_url": base_url, "kind": kind, "label": "Local server"})
    assert response.status_code == 200
    assert response.json()["kind"] == "openai-compatible"
    host = base_url.split("//")[1].split(":")[0]
    assert [(e.base_url, e.kind) for e in store.list()] == [(f"http://{host}:1234/v1", "openai-compatible")]
    assert model_endpoints.chosen_backend_url(store, None) is None


def test_an_ollama_added_with_its_openai_path_is_kept_as_ollama_and_used_at_its_root(client, monkeypatch, tmp_path):
    monkeypatch.setattr(routes.config, "ollama_base_url", None)
    store = stored_endpoints(monkeypatch, tmp_path)
    model_server_statuses(monkeypatch, OLLAMA_SERVER)
    response = client.post("/api/model-endpoints", json={"base_url": "http://localhost:11434/v1", "kind": "ollama"})
    assert response.status_code == 200
    assert [(e.base_url, e.kind) for e in store.list()] == [("http://localhost:11434/v1", "ollama")]
    assert model_endpoints.chosen_backend_url(store, None) == "http://localhost:11434"


def test_adding_a_local_server_that_is_already_stored_under_its_openai_path_is_refused(client, monkeypatch, tmp_path):
    store = stored_endpoints(monkeypatch, tmp_path)
    store.add("http://127.0.0.1:1234/v1", "openai-compatible")
    model_server_statuses(monkeypatch, OPENAI_ONLY_SERVER)
    response = client.post("/api/model-endpoints", json={"base_url": "http://127.0.0.1:1234", "kind": "openai-compatible"})
    assert response.status_code == 409
    assert len(store.list()) == 1


def test_start_ollama_does_not_launch_a_server_starfire_would_not_use(client, monkeypatch, tmp_path):
    monkeypatch.setattr(routes.config, "ollama_base_url", "http://gpu-box:11434")
    stored_endpoints(monkeypatch, tmp_path)
    launched = []

    async def launch(*command, **_options):
        launched.append(command)

    monkeypatch.setattr(routes.ollama_manager, "is_installed", lambda: True)
    monkeypatch.setattr(routes.ollama_manager.asyncio, "create_subprocess_exec", launch)
    model_server_statuses(monkeypatch, {})
    response = client.post("/api/hardware/ollama/start")
    assert response.status_code == 502
    assert "http://gpu-box:11434" in response.json()["detail"]
    assert launched == []
