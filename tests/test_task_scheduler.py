import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import agent_loop
import auth
import auth_routes
import routes
import task_scheduler
import user_data
from auth import SessionStore, UserStore
from model_endpoints import ModelEndpointStore
from task_scheduler import TaskScheduler, compute_next_run
from task_store import ScheduledTask, TaskRunStore, TaskStore

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}
ALICE = "a" * 32
BOB = "b" * 32
WEDNESDAY_NOON = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


def parsed(value: str) -> datetime:
    return datetime.fromisoformat(value)


@pytest.mark.parametrize("schedule, time, day, expected", [
    ("daily", "13:30", "", datetime(2026, 11, 11, 13, 30, tzinfo=timezone.utc)),
    ("daily", "08:00", "", datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)),
    ("once", "12:00", "", datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)),
    ("weekly", "09:00", "Friday", datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)),
    ("weekly", "09:00", "wednesday", datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)),
    ("weekly", "13:00", "someday", datetime(2026, 11, 11, 13, 0, tzinfo=timezone.utc)),
    ("daily", "", "", datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc)),
])
def test_the_next_run_is_the_first_matching_time_after_now(schedule, time, day, expected):
    assert parsed(compute_next_run(schedule, time, day, after=WEDNESDAY_NOON)) == expected


def test_a_time_that_is_not_hh_mm_falls_back_to_nine_and_says_so(caplog):
    next_run = parsed(compute_next_run("daily", "half past", after=WEDNESDAY_NOON))
    assert (next_run.hour, next_run.minute) == (9, 0)
    assert "half past" in caplog.text


def test_cron_schedules_follow_the_expression():
    assert parsed(compute_next_run("cron", cron_expression="15 */2 * * *", after=WEDNESDAY_NOON)) == \
        datetime(2026, 11, 11, 12, 15, tzinfo=timezone.utc)


@pytest.mark.parametrize("schedule, time, cron", [
    ("cron", "", ""),
    ("hourly", "", ""),
    ("daily", "25:00", ""),
])
def test_impossible_schedules_are_refused(schedule, time, cron):
    with pytest.raises(ValueError):
        compute_next_run(schedule, time, cron_expression=cron, after=WEDNESDAY_NOON)


@pytest.fixture
def per_user_stores(tmp_path):
    user_data.configure(str(tmp_path))
    return TaskScheduler(user_data.PerUser("tasks"), user_data.PerUser("task_runs"), None,
                         user_ids_fn=lambda: user_data.registry.user_ids())


def add_task(user_id, prompt, **fields):
    with user_data.run_as(user_id):
        return user_data.PerUser("tasks").add(ScheduledTask(id="", name=prompt, prompt=prompt, **fields))


def past(minutes=5) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def future(days=1) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def stored(user_id, task_id):
    with user_data.run_as(user_id):
        return user_data.PerUser("tasks").get(task_id), user_data.PerUser("task_runs").for_task(task_id)


def test_due_tasks_run_as_their_own_account_and_nothing_else_runs(per_user_stores):
    ran = []

    async def chat(endpoint_id, model, prompt, servers, tools):
        ran.append((user_data.current_user_id.get(), prompt))
        return f"done: {prompt}"

    per_user_stores.chat_fn = chat
    alice_daily = add_task(ALICE, "alice daily", schedule="daily", scheduled_time="07:00", next_run=past())
    add_task(ALICE, "alice later", schedule="daily", next_run=future())
    add_task(ALICE, "alice paused", schedule="daily", status="paused", next_run=past())
    bob_once = add_task(BOB, "bob once", schedule="once", next_run=past())

    asyncio.run(per_user_stores._tick())

    assert sorted(ran) == [(ALICE, "alice daily"), (BOB, "bob once")]
    task, runs = stored(ALICE, alice_daily.id)
    assert task.run_count == 1 and task.status == "active" and task.next_run > datetime.now(timezone.utc).isoformat()
    assert [(r.status, r.output) for r in runs] == [("ok", "done: alice daily")]
    task, runs = stored(BOB, bob_once.id)
    assert task.status == "paused" and task.run_count == 1
    with user_data.run_as(ALICE):
        assert user_data.PerUser("task_runs").for_task(bob_once.id) == []


def test_a_failing_task_is_recorded_as_an_error_and_still_moves_on(per_user_stores):
    async def chat(*args):
        raise RuntimeError("model server is down")

    per_user_stores.chat_fn = chat
    task = add_task(ALICE, "check", schedule="daily", next_run=past())
    asyncio.run(per_user_stores._tick())
    after, runs = stored(ALICE, task.id)
    assert [(r.status, r.output) for r in runs] == [("error", "model server is down")]
    assert after.run_count == 1 and after.next_run > datetime.now(timezone.utc).isoformat()


def test_one_accounts_broken_tasks_do_not_stop_another_accounts_tasks(per_user_stores, monkeypatch):
    ran = []

    async def chat(endpoint_id, model, prompt, servers, tools):
        ran.append(prompt)
        return "ok"

    per_user_stores.chat_fn = chat
    add_task(ALICE, "alice", schedule="daily", next_run=past())
    add_task(BOB, "bob", schedule="daily", next_run=past())
    real_list = TaskStore.list

    def broken_for_alice(store):
        if user_data.current_user_id.get() == ALICE:
            raise OSError("disk error")
        return real_list(store)

    monkeypatch.setattr(TaskStore, "list", broken_for_alice)
    asyncio.run(per_user_stores._tick())
    assert ran == ["bob"]


def test_the_loop_keeps_ticking_after_a_failure_and_stops_when_asked(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(task_scheduler, "TICK_SECONDS", 0.01)
    scheduler = TaskScheduler(TaskStore(str(tmp_path)), TaskRunStore(str(tmp_path)), None)
    ticks = []

    async def tick():
        ticks.append(1)
        if len(ticks) == 1:
            raise OSError("first tick fails")
        if len(ticks) == 3:
            scheduler.stop()

    scheduler._tick = tick
    asyncio.run(asyncio.wait_for(scheduler.run_loop(), timeout=5))
    assert len(ticks) == 3
    assert "first tick fails" in caplog.text


@pytest.fixture
def endpoint_and_users(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    users = UserStore(data_dir)
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(routes, "endpoints", ModelEndpointStore(data_dir))
    monkeypatch.setattr(routes, "api_keys", SimpleNamespace(load=lambda: {}))
    user_data.configure(data_dir)
    hashed = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    admin = users.upsert("admin@example.com", "Admin", "admin", hashed)
    user = users.upsert("sam@example.com", "Sam", "user", hashed)
    endpoint = routes.endpoints.add("http://127.0.0.1:11434", kind="ollama")
    seen = []

    async def collected(base_url, api_key, model, messages, **kwargs):
        seen.append(kwargs["ctx"].may_use_host_tools)
        return "ok"

    monkeypatch.setattr(agent_loop, "run_chat_collected", collected)
    return SimpleNamespace(admin=admin, user=user, endpoint=endpoint, seen=seen, users=users)


def run_task_chat(user_id, endpoint_id):
    async def go():
        with user_data.run_as(user_id):
            return await routes._task_chat_fn(endpoint_id, "qwen3:14b", "tidy the server", [], ["run_shell"])
    return asyncio.run(go())


def test_a_scheduled_task_only_gets_host_tools_for_an_active_admin(endpoint_and_users):
    env = endpoint_and_users
    run_task_chat(env.admin.id, env.endpoint.id)
    run_task_chat(env.user.id, env.endpoint.id)
    env.users.create("second@example.com", "Second", PASSWORD, role="admin")
    env.users.update(env.admin.id, role="user")
    run_task_chat(env.admin.id, env.endpoint.id)
    assert env.seen == [True, False, False]


def test_a_scheduled_task_without_a_known_endpoint_fails_clearly(endpoint_and_users):
    with pytest.raises(RuntimeError, match="no valid endpoint"):
        routes._task_chat_fn("gone", "m", "p", [], [])


@pytest.fixture
def client(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    users, sessions = UserStore(data_dir), SessionStore(data_dir)
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(auth_routes, "known_addresses", auth.KnownAddresses(data_dir))
    monkeypatch.setattr(routes, "registry", user_data.configure(data_dir))
    users.upsert("sam@example.com", "Sam", "user", bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode())
    app = FastAPI()
    auth.install(app, users, sessions)
    app.include_router(routes.router)
    app.include_router(auth_routes.router)
    test_client = TestClient(app, base_url="https://testserver", headers=HEADERS)
    assert test_client.post("/api/auth/login", json={"email": "sam@example.com", "password": PASSWORD}).status_code == 200
    return test_client


@pytest.mark.parametrize("body", [
    {"prompt": "   ", "schedule": "daily"},
    {"prompt": "x", "schedule": "hourly"},
    {"prompt": "x", "schedule": "cron"},
    {"prompt": "x", "schedule": "cron", "cron_expression": "not a cron line"},
    {"prompt": "x", "schedule": "daily", "scheduled_time": "25:61"},
])
def test_a_task_with_an_impossible_schedule_is_refused_and_not_saved(client, body):
    response = client.post("/api/tasks", json=body)
    assert response.status_code == 400, response.text
    assert client.get("/api/tasks").json()["tasks"] == []


def test_running_a_task_now_records_a_run_and_counts_it(client, monkeypatch):
    async def chat(endpoint_id, model, prompt, servers, tools):
        return "answer"

    monkeypatch.setattr(routes.scheduler, "chat_fn", chat)
    task_id = client.post("/api/tasks", json={"prompt": "hello", "schedule": "daily"}).json()["id"]
    assert client.post(f"/api/tasks/{task_id}/run").json() == {"ok": True}
    assert [r["output"] for r in client.get(f"/api/tasks/{task_id}/runs").json()["runs"]] == ["answer"]
    assert client.get("/api/tasks").json()["tasks"][0]["run_count"] == 1
    assert [r["task_id"] for r in client.get("/api/tasks/runs/recent").json()["runs"]] == [task_id]
