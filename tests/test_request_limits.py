import asyncio
import io
import os
import sys
import threading
import time

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfWriter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import auth
import auth_routes
import chat_stream
import code_check
import documents_store
import routes
import user_data
from auth import SessionStore, UserStore
from config import config
from model_endpoints import ModelEndpointStore

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}


@pytest.fixture
def admin(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    users, sessions = UserStore(data_dir), SessionStore(data_dir)
    monkeypatch.setattr(config, "data_dir", data_dir)
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(auth_routes, "known_addresses", auth.KnownAddresses(data_dir))
    monkeypatch.setattr(routes, "registry", user_data.configure(data_dir))
    monkeypatch.setattr(routes, "endpoints", ModelEndpointStore(data_dir))
    users.upsert("admin@example.com", "Admin", "admin", bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode())
    app = FastAPI()
    auth.install(app, users, sessions)
    app.include_router(auth_routes.router)
    app.include_router(routes.router)
    client = TestClient(app, base_url="https://testserver", headers=HEADERS, raise_server_exceptions=False)
    assert client.post("/api/auth/login", json={"email": "admin@example.com", "password": PASSWORD}).status_code == 200
    return client


@pytest.fixture
def sent_options(monkeypatch):
    seen = []

    async def fake_stream(base_url, api_key, model, messages, system, options):
        seen.append(options)
        yield chat_stream._sse_line({"done": True})

    monkeypatch.setattr(routes, "stream_chat", fake_stream)
    return seen


def chat(client, options):
    endpoint = routes.endpoints.add("https://models.example.com/v1", kind="api-key")
    return client.post("/api/chat_stream", json={"endpoint_id": endpoint.id, "model": "gpt-4o", "options": options,
                                                 "messages": [{"role": "user", "content": "hi"}]})


@pytest.mark.parametrize("options", [{"num_ctx": "lots"}, {"num_ctx": 10}, {"temperature": 9},
                                     {"stop": ["x"]}, {"num_gpu": 99}])
def test_a_chat_with_a_bad_or_unknown_setting_is_refused_as_a_client_error(admin, sent_options, options):
    response = chat(admin, options)
    assert response.status_code == 400 and sent_options == []
    assert response.json()["detail"].startswith("chat settings not accepted - ")
    assert next(iter(options)) in response.json()["detail"]


def test_the_page_settings_still_reach_the_model(admin, sent_options):
    settings = {"temperature": 0.4, "top_p": 0.9, "top_k": 40, "num_predict": 512, "num_ctx": 8192,
                "repeat_penalty": 1.1, "seed": 7}
    assert chat(admin, settings).status_code == 200
    assert sent_options == [settings]


def held_checks(monkeypatch):
    monkeypatch.setattr(code_check, "check_slots", code_check.CheckSlots())
    release = threading.Event()
    started = threading.Semaphore(0)

    def slow_check(language, code, hidden_dirs=()):
        started.release()
        release.wait(5)
        return code_check._result("python", "ok", "test", "")

    monkeypatch.setattr(code_check, "check_sync", slow_check)
    return release, started


def test_only_two_code_checks_run_at_once_and_a_finished_one_frees_its_place(monkeypatch):
    release, started = held_checks(monkeypatch)

    async def three_at_once():
        running = [asyncio.create_task(code_check.check("python", "x = 1", account=account))
                   for account in ("first", "second")]
        await asyncio.to_thread(started.acquire)
        await asyncio.to_thread(started.acquire)
        third = await code_check.check("python", "x = 1", account="third")
        release.set()
        return third, await asyncio.gather(*running), await code_check.check("python", "x = 1", account="third")

    third, first_two, after = asyncio.run(three_at_once())
    assert third["message"] == code_check.CHECKS_BUSY and third["status"] == "skipped"
    assert [result["status"] for result in first_two] == ["ok", "ok"] and after["status"] == "ok"


def test_one_account_runs_one_code_check_at_a_time_and_leaves_the_other_place_free(monkeypatch):
    release, started = held_checks(monkeypatch)

    async def one_account_twice_then_another():
        running = asyncio.create_task(code_check.check("python", "x = 1", account="busy"))
        await asyncio.to_thread(started.acquire)
        second_from_busy = await code_check.check("python", "x = 1", account="busy")
        other = asyncio.create_task(code_check.check("python", "x = 1", account="other"))
        await asyncio.to_thread(started.acquire)
        release.set()
        return second_from_busy, await running, await other

    second_from_busy, first, other = asyncio.run(one_account_twice_then_another())
    assert second_from_busy["status"] == "skipped"
    assert second_from_busy["message"] == code_check.ACCOUNT_CHECK_RUNNING
    assert first["status"] == "ok" and other["status"] == "ok"


def test_a_code_check_is_counted_against_the_signed_in_account(admin, monkeypatch):
    counted = []

    async def recording_check(language, code, hidden_dirs=(), *, account):
        counted.append(account)
        return code_check._result("python", "ok", "test", "")

    monkeypatch.setattr(code_check, "check", recording_check)
    assert admin.post("/api/code-check", json={"language": "python", "code": "x = 1"}).status_code == 200
    assert counted == [admin.get("/api/auth/me").json()["id"]]


def pdf_with_pages(count):
    writer = PdfWriter()
    for _ in range(count):
        writer.add_blank_page(width=72, height=72)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_a_document_is_read_away_from_the_request_loop(admin, monkeypatch):
    threads = []
    real_add = documents_store.DocumentStore.add

    def recording_add(self, filename, raw):
        threads.append(threading.current_thread())
        return real_add(self, filename, raw)

    monkeypatch.setattr(documents_store.DocumentStore, "add", recording_add)
    response = admin.post("/api/documents", files={"file": ("notes.txt", b"Some text worth keeping.")})
    assert response.status_code == 200
    assert threads[0].name.startswith("asyncio_")


def test_a_pdf_with_too_many_pages_is_refused_without_reading_them(admin, monkeypatch):
    monkeypatch.setattr(documents_store, "MAX_PDF_PAGES", 3)
    response = admin.post("/api/documents", files={"file": ("big.pdf", pdf_with_pages(4), "application/pdf")})
    assert response.status_code == 400 and "more than 3 pages" in response.json()["detail"]


def test_documents_stop_at_the_per_account_total(admin, monkeypatch):
    monkeypatch.setattr(config, "max_documents_mb", 1)
    first = admin.post("/api/documents", files={"file": ("a.txt", b"word " * 150_000)})
    assert first.status_code == 200
    second = admin.post("/api/documents", files={"file": ("b.txt", b"word " * 150_000)})
    assert second.status_code == 413 and "limited to 1 MB" in second.json()["detail"]


SLOW_CHECK_SECONDS = 0.3


def test_an_account_that_used_its_checker_time_for_the_minute_waits_while_others_still_check(monkeypatch):
    monkeypatch.setattr(code_check, "check_slots", code_check.CheckSlots())
    monkeypatch.setattr(code_check, "MAX_CHECK_SECONDS_PER_ACCOUNT_PER_MINUTE", SLOW_CHECK_SECONDS / 2, raising=False)

    def slow_check(language, code, hidden_dirs=()):
        time.sleep(SLOW_CHECK_SECONDS)
        return code_check._result("python", "ok", "test", "")

    monkeypatch.setattr(code_check, "check_sync", slow_check)

    async def checks():
        first = await code_check.check("python", "x = 1", account="busy")
        again = await code_check.check("python", "x = 1", account="busy")
        other = await code_check.check("python", "x = 1", account="other")
        return first, again, other

    first, again, other = asyncio.run(checks())
    assert first["status"] == "ok" and other["status"] == "ok"
    assert again["status"] == "skipped" and "minute" in again["message"], again
