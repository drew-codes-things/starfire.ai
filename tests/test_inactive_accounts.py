import asyncio
import os
import sys

import bcrypt
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import auth_routes
import routes
import user_data
from auth import UserStore
from email_rule_checker import EmailRuleChecker
from task_scheduler import TaskScheduler


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    users = UserStore(str(tmp_path))
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(routes, "registry", user_data.configure(str(tmp_path)))
    hashed = bcrypt.hashpw(b"long enough password", bcrypt.gensalt(rounds=4)).decode()
    users.upsert("admin@example.com", "Admin", "admin", hashed)
    active = users.upsert("on@example.com", "On", "user", hashed)
    disabled = users.upsert("off@example.com", "Off", "user", hashed)
    users.update(disabled.id, disabled=True)
    gone = "f" * 32
    for user_id in (active.id, disabled.id, gone):
        os.makedirs(tmp_path / "users" / user_id)
    return {"active": active.id, "disabled": disabled.id, "gone": gone}


def test_only_active_accounts_have_their_automations_and_email_rules_run(accounts):
    assert routes._active_account_ids() == [accounts["active"]]
    assert routes.scheduler.user_ids_fn() == [accounts["active"]]
    assert routes.email_rule_checker.user_ids_fn() == [accounts["active"]]


class Recorder:
    def __init__(self):
        self.seen = []

    def list(self, *args, **kwargs):
        self.seen.append(user_data.current_user_id.get())
        return []


def test_a_tick_never_runs_as_a_disabled_or_deleted_account(accounts):
    tasks, rules = Recorder(), Recorder()
    asyncio.run(TaskScheduler(tasks, None, None, user_ids_fn=routes._active_account_ids)._tick())
    asyncio.run(EmailRuleChecker(rules, None, None, None, None, user_ids_fn=routes._active_account_ids)._tick())
    assert tasks.seen == [accounts["active"]] and rules.seen == [accounts["active"]]
