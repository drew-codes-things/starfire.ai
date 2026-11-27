import asyncio
import json
import os
import sys

import httpx
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chat_stream
import reply_runs
import routes
import user_data
from agent_loop import run_chat_collected
from config import config
import test_chat_routing
from test_chat_routing import login

app = test_chat_routing.app

pytestmark = pytest.mark.usefixtures("unreadable_model_lists")

ENGINE = "http://engine.local:11438"
MAIN = "hf.co/owner/big-a3b-gguf:iq4_xs"
SMALL = "qwen3:14b"
HELLO = [{"role": "user", "content": "hello"}]
LOAD_FAILED = '{"error":"the model failed to load"}'
TOO_LONG = '{"error":"the request exceeds the available context size, try increasing it"}'


def reply_lines(text: str) -> str:
    return "\n".join(json.dumps(line) for line in [{"message": {"content": text}}, {"done": True}])


@pytest.fixture
def engine(monkeypatch):
    asked, answers = [], {}

    def handle(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        asked.append(model)
        answer = answers.get(model, (200, reply_lines(f"from {model}")))
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        return httpx.Response(status, text=body)

    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(chat_stream.httpx, "AsyncClient", client)
    monkeypatch.setattr(config, "ollama_base_url", ENGINE)
    monkeypatch.setattr(config, "task_model", MAIN)
    return asked, answers


def streamed(model=MAIN, base_url=ENGINE):
    async def collect():
        return [chunk async for chunk in chat_stream.stream_chat(base_url, None, model, HELLO, None, None)]
    return [json.loads(chunk.decode()[len("data: "):]) for chunk in asyncio.run(collect())]


def main_down(reason):
    return {"error": f"The main model couldn't answer just now ({reason}). Try again in a moment, "
                     "or pick a different model in the model list."}


def test_a_main_model_that_fails_to_load_says_so_and_no_other_model_answers(engine):
    asked, answers = engine
    answers[MAIN] = (500, LOAD_FAILED)
    assert streamed() == [main_down("the engine answered 500: the model failed to load")]
    assert asked == [MAIN]


def test_an_error_line_before_any_text_says_so_and_no_other_model_answers(engine):
    asked, answers = engine
    answers[MAIN] = (200, LOAD_FAILED + "\n")
    assert streamed() == [main_down("the model failed to load")]
    assert asked == [MAIN]


def test_an_engine_that_cannot_be_reached_says_so_and_no_other_model_answers(engine):
    asked, answers = engine
    answers[MAIN] = httpx.ConnectError("connection refused")
    assert streamed() == [main_down("the AI engine could not be reached")]
    assert asked == [MAIN]


def test_a_slow_main_model_is_reported_as_slow_not_as_down(engine):
    asked, answers = engine
    answers[MAIN] = httpx.ReadTimeout("timed out")
    [event] = streamed()
    assert list(event) == ["error"] and "main model couldn't answer" not in str(event["error"]) and asked == [MAIN]


def test_an_error_after_reply_text_is_shown_as_it_is(engine):
    asked, answers = engine
    answers[MAIN] = (200, json.dumps({"message": {"content": "Half"}}) + "\n" + LOAD_FAILED + "\n")
    events = streamed()
    assert events[0] == {"delta": "Half"} and list(events[-1]) == ["error"] and asked == [MAIN]
    assert "main model couldn't answer" not in str(events[-1]["error"])


@pytest.mark.parametrize("status, body", [(400, TOO_LONG), (500, TOO_LONG), (400, '{"error":"bad request"}'),
                                          (404, '{"error":"model not found"}')])
def test_requests_the_engine_refused_for_their_own_reasons_keep_their_own_error(engine, status, body):
    asked, answers = engine
    answers[MAIN] = (status, body)
    [event] = streamed()
    assert list(event) == ["error"] and "main model couldn't answer" not in str(event["error"]) and asked == [MAIN]


def test_other_models_and_other_connections_keep_their_own_error(engine):
    asked, answers = engine
    answers["other:9b"] = (500, LOAD_FAILED)
    answers[MAIN] = (500, LOAD_FAILED)
    for event in (streamed(model="other:9b")[-1], streamed(base_url="https://gateway.example.com/v1")[-1]):
        assert list(event) == ["error"] and "main model couldn't answer" not in str(event["error"])
    assert asked == ["other:9b", MAIN]


def test_a_helper_job_on_a_failed_main_model_never_runs_on_another_model(engine):
    asked, answers = engine
    answers[MAIN] = (500, LOAD_FAILED)
    with pytest.raises(Exception, match="main model couldn't answer"):
        asyncio.run(run_chat_collected(ENGINE, None, MAIN, HELLO))
    assert asked == [MAIN]


def test_the_next_request_after_a_failure_asks_the_main_again(engine):
    asked, answers = engine
    answers[MAIN] = (500, LOAD_FAILED)
    streamed()
    del answers[MAIN]
    assert streamed() == [{"delta": f"from {MAIN}"}, {"done": True}]
    assert asked == [MAIN, MAIN]


def test_the_chat_route_shows_the_plain_message_and_saves_no_reply(app, engine):  # noqa: F811
    asked, answers = engine
    answers[MAIN] = (500, LOAD_FAILED)
    endpoint = routes.endpoints.add(ENGINE, kind="ollama")
    admin = login(app, "admin@example.com")
    session = admin.post("/api/chat/sessions", json={"messages": HELLO}).json()
    response = admin.post("/api/chat_stream", json={"endpoint_id": endpoint.id, "model": MAIN, "messages": HELLO,
                                                    "session_id": session["id"], "reply_index": 1})
    events = [json.loads(line[len("data:"):]) for line in response.text.splitlines() if line.startswith("data:")]
    assert main_down("the engine answered 500: the model failed to load") in events
    stored = admin.get(f"/api/chat/sessions/{session['id']}").json()["messages"]
    assert [message["role"] for message in stored] == ["user"] and asked == [MAIN]


def test_a_saved_reply_from_the_main_model_keeps_the_main_model(app, engine):  # noqa: F811
    endpoint = routes.endpoints.add(ENGINE, kind="ollama")
    admin = login(app, "admin@example.com")
    session = admin.post("/api/chat/sessions", json={"messages": HELLO}).json()
    admin.post("/api/chat_stream", json={"endpoint_id": endpoint.id, "model": MAIN, "messages": HELLO,
                                         "session_id": session["id"], "reply_index": 1})
    stored = admin.get(f"/api/chat/sessions/{session['id']}").json()["messages"]
    assert stored[-1]["content"] == f"from {MAIN}" and stored[-1]["model"] == MAIN


def test_a_chat_context_length_never_reaches_the_main_model(app, engine, monkeypatch):  # noqa: F811
    sent = []

    async def capture(client, url, headers, payload, max_bytes=None):
        sent.append(payload)
        yield ("delta", "ok")

    monkeypatch.setattr(chat_stream, "_iter_ollama", capture)
    monkeypatch.setattr(config, "task_num_ctx", 24576)
    endpoint = routes.endpoints.add(ENGINE, kind="ollama")
    admin = login(app, "admin@example.com")
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 4000} for i in range(9)]
    admin.post("/api/chat_stream", json={"endpoint_id": endpoint.id, "model": MAIN, "messages": history,
                                         "options": {"num_ctx": 1024}})
    [payload] = sent
    assert payload["options"]["num_ctx"] == 24576
    assert sum(m["content"] == "x" * 4000 for m in payload["messages"]) == len(history)


@pytest.mark.parametrize("error", [httpx.ConnectTimeout("connect timed out"), httpx.PoolTimeout("pool timed out")])
def test_a_timeout_while_connecting_is_shown_as_a_timeout(engine, error):
    asked, answers = engine
    answers[MAIN] = error
    [event] = streamed()
    assert asked == [MAIN] and list(event) == ["error"] and "main model couldn't answer" not in str(event["error"])


BUSY_CARD = ("no room on the GPU for {name}: every loaded model is busy",
             "{name} is still answering another request with different settings; try again when it is done")


@pytest.mark.parametrize("text", BUSY_CARD)
def test_a_busy_card_is_waited_out_and_the_main_asked_again(engine, monkeypatch, text):
    asked, answers = engine
    waits = []

    async def wait(seconds):
        waits.append(seconds)
        answers.pop(MAIN, None)

    monkeypatch.setattr(chat_stream, "wait_for_a_busy_card", wait)
    answers[MAIN] = (500, json.dumps({"error": text.format(name=MAIN)}))
    assert streamed() == [{"delta": f"from {MAIN}"}, {"done": True}]
    assert asked == [MAIN, MAIN] and waits == [chat_stream.BUSY_CARD_WAIT_SECONDS]


@pytest.mark.parametrize("text", BUSY_CARD)
def test_a_card_that_stays_busy_is_reported_as_busy(engine, monkeypatch, text):
    asked, answers = engine

    async def wait(seconds):
        return None

    monkeypatch.setattr(chat_stream, "wait_for_a_busy_card", wait)
    answers[MAIN] = (500, json.dumps({"error": text.format(name=MAIN)}))
    [event] = streamed()
    assert asked == [MAIN] * (chat_stream.BUSY_CARD_RETRIES + 1)
    assert list(event) == ["error"] and "main model couldn't answer" not in str(event["error"])


BRAIN = "http://brain.local:8080/v1"


def brain_stream(report: dict) -> str:
    chunks = [{"choices": [{"index": 0, "delta": {"role": "assistant", "content": "from the brain"}}]},
              {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "arty": report}]
    return "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"


@pytest.fixture
def brain_reports(monkeypatch):
    report = {}
    real_client = httpx.AsyncClient

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=brain_stream(report), headers={"content-type": "text/event-stream"})

    def client(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(chat_stream.httpx, "AsyncClient", client)
    return report


@pytest.mark.parametrize("report", [{"model": SMALL, "fallback": True, "steps": 0}, {"model": MAIN, "steps": 0},
                                    {"steps": 0}])
def test_an_arty_reply_is_saved_as_the_model_the_chat_asked_for(brain_reports, report):
    brain_reports.update(report)
    events = streamed("Arty", BRAIN)
    assert not any("fallback" in event for event in events)
    saved = reply_runs.finished_reply([chat_stream._sse_line(event) for event in events], "Arty")
    assert saved["model"] == "Arty" and saved["content"] == "from the brain"


def test_a_helper_job_cut_off_at_its_length_limit_returns_its_text_without_a_note(app, engine):  # noqa: F811
    asked, answers = engine
    cut = "\n".join(json.dumps(line) for line in [{"message": {"content": "A short title"}},
                                                    {"done": True, "done_reason": "length"}])
    answers[MAIN] = (200, cut)
    routes.endpoints.add(ENGINE, kind="ollama")
    admin = login(app, "admin@example.com")
    endpoint = routes.endpoints.list()[0]
    reply = admin.post("/api/quick-complete", json={"endpoint_id": endpoint.id, "model": MAIN, "prompt": "Name this chat",
                                                    "helper": True})
    assert reply.status_code == 200 and reply.json()["text"] == "A short title"


OLD_NAME = "own/old-model:9b"


@pytest.fixture
def answering_hosts(monkeypatch):
    asked = []
    real_client = httpx.AsyncClient

    def handle(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        asked.append((request.url.host, model))
        if request.url.host == "brain.local":
            return httpx.Response(200, text=brain_stream({"model": MAIN, "steps": 0}),
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, text=reply_lines(f"from {model}"))

    def client(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(chat_stream.httpx, "AsyncClient", client)
    return asked


def test_a_model_renamed_to_arty_is_sent_to_arty_s_connection(app, answering_hosts, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "model_renames", json.dumps({OLD_NAME: "Arty"}))
    monkeypatch.setattr(config, "arty_base_url", BRAIN)
    engine = routes.endpoints.add(ENGINE, kind="ollama")
    routes.endpoints.add(BRAIN, kind="api-key")
    admin = login(app, "admin@example.com")
    response = admin.post("/api/chat_stream", json={"endpoint_id": engine.id, "model": OLD_NAME, "messages": HELLO})
    assert response.status_code == 200 and answering_hosts == [("brain.local", "Arty")]


def test_a_model_renamed_to_arty_with_no_arty_connection_is_refused_clearly(app, answering_hosts,
                                                                             monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "model_renames", json.dumps({OLD_NAME: "Arty"}))
    monkeypatch.setattr(config, "arty_base_url", "")
    engine = routes.endpoints.add(ENGINE, kind="ollama")
    admin = login(app, "admin@example.com")
    response = admin.post("/api/chat_stream", json={"endpoint_id": engine.id, "model": OLD_NAME, "messages": HELLO})
    assert response.status_code == 409 and "Arty" in response.json()["detail"] and answering_hosts == []


def test_a_scheduled_task_naming_a_renamed_model_runs_on_the_new_name(app, answering_hosts, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "model_renames", json.dumps({OLD_NAME: SMALL}))
    engine = routes.endpoints.add(ENGINE, kind="ollama")
    admin_id = login(app, "admin@example.com").get("/api/auth/me").json()["id"]
    with user_data.run_as(admin_id):
        asyncio.run(routes._task_chat_fn(engine.id, OLD_NAME, "Say hello", [], []))
    assert answering_hosts == [("engine.local", SMALL)]


def test_a_saved_choice_and_chat_renamed_to_arty_open_on_arty_s_connection(app, answering_hosts,
                                                                          monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "arty_base_url", BRAIN)
    engine = routes.endpoints.add(ENGINE, kind="ollama")
    brain = routes.endpoints.add(BRAIN, kind="api-key")
    admin = login(app, "admin@example.com")
    assert admin.put("/api/model-choice", json={"endpoint_id": engine.id, "model": OLD_NAME}).status_code == 200
    session = admin.post("/api/chat/sessions", json={"title": "old"}).json()
    admin.put(f"/api/chat/sessions/{session['id']}", json={"endpoint_id": engine.id, "model": OLD_NAME})
    monkeypatch.setattr(config, "model_renames", json.dumps({OLD_NAME: "Arty"}))
    assert admin.get("/api/model-choice").json() == {"endpoint_id": brain.id, "model": "Arty"}
    opened = admin.get(f"/api/chat/sessions/{session['id']}").json()
    assert (opened["endpoint_id"], opened["model"]) == (brain.id, "Arty")


QWEN_MAIN = "hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-IQ4_XS"


@pytest.fixture
def sent_payloads(monkeypatch):
    sent = []
    real_client = httpx.AsyncClient

    def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        sent.append(payload)
        return httpx.Response(200, text=reply_lines(f"from {payload['model']}"))

    def client(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(chat_stream.httpx, "AsyncClient", client)
    monkeypatch.setattr(config, "ollama_base_url", ENGINE)
    monkeypatch.setattr(config, "task_model", QWEN_MAIN)
    return sent


def test_a_chat_on_a_qwen3_model_is_sent_with_thinking_off(app, sent_payloads):  # noqa: F811
    endpoint = routes.endpoints.add(ENGINE, kind="ollama")
    admin = login(app, "admin@example.com")
    for model in (QWEN_MAIN, SMALL):
        admin.post("/api/chat_stream", json={"endpoint_id": endpoint.id, "model": model, "messages": HELLO})
    assert [payload["think"] for payload in sent_payloads] == [False, False]
    assert "think" not in routes.ChatOptions.model_fields


def test_a_helper_job_on_a_qwen3_main_never_thinks(app, sent_payloads):  # noqa: F811
    endpoint = routes.endpoints.add(ENGINE, kind="ollama")
    admin = login(app, "admin@example.com")
    reply = admin.post("/api/quick-complete", json={"endpoint_id": endpoint.id, "model": QWEN_MAIN,
                                                    "prompt": "Name this chat", "helper": True})
    assert reply.status_code == 200 and sent_payloads[-1]["model"] == QWEN_MAIN
    assert sent_payloads[-1]["think"] is False and sent_payloads[-1]["options"]["num_predict"] == 1024


def test_a_caller_that_asks_for_thinking_gets_it(sent_payloads):
    async def collect(**kwargs):
        return [item async for item in chat_stream.stream_chat_once(ENGINE, None, SMALL, HELLO, None, None, **kwargs)]

    asyncio.run(collect(think=True))
    asyncio.run(collect())
    assert [payload["think"] for payload in sent_payloads] == [True, False]
