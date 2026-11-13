import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import email_client
import email_rule_checker
import user_data
from email_rule_checker import EmailRuleChecker
from email_store import EmailAccountStore, EmailRuleStore
from note_store import NoteStore

ALICE = "a" * 32
BOB = "b" * 32
INBOX = [
    {"uid": "3", "from": "Old Boss <boss@work.example>", "subject": "already seen"},
    {"uid": "7", "from": "Boss <BOSS@work.example>", "subject": "Quarterly numbers"},
    {"uid": "8", "from": "friend@home.example", "subject": "Boss fight tonight?"},
    {"uid": "not-a-number", "from": "boss@work.example", "subject": "broken"},
    {"from": "boss@work.example", "subject": "no uid"},
]


@pytest.fixture
def mailbox(monkeypatch):
    calls = []
    state = {"inbox": list(INBOX), "fail": set()}

    def recorded(name, result=None):
        def call(account, password, *args):
            if name in state["fail"]:
                raise OSError(f"{name} failed")
            calls.append((name, account.email_address, password, *args))
            return result() if callable(result) else result
        return call

    monkeypatch.setattr(email_client, "list_messages", recorded("list_messages", lambda: state["inbox"]))
    monkeypatch.setattr(email_client, "mark_read", recorded("mark_read"))
    monkeypatch.setattr(email_client, "archive_message", recorded("archive_message"))
    monkeypatch.setattr(email_client, "delete_message", recorded("delete_message"))
    monkeypatch.setattr(email_client, "read_message", recorded(
        "read_message", {"from": "Boss <boss@work.example>", "subject": "Quarterly numbers", "body": "Numbers are up."}))
    return SimpleNamespace(calls=calls, state=state)


@pytest.fixture
def setup(tmp_path):
    accounts = EmailAccountStore(str(tmp_path))
    rules = EmailRuleStore(str(tmp_path))
    notes = NoteStore(str(tmp_path))
    account = accounts.add("Work", "me@work.example", "imap.work.example", 993, "smtp.work.example", 587)
    passwords = {account.id: "mail-pass"}
    prompts = []

    async def chat(endpoint_id, model, prompt):
        prompts.append((endpoint_id, model, prompt))
        return "Short summary."

    checker = EmailRuleChecker(rules, accounts, passwords.get, chat, notes)
    return SimpleNamespace(accounts=accounts, rules=rules, notes=notes, account=account, passwords=passwords,
                           prompts=prompts, checker=checker)


def add_rule(setup, **fields):
    defaults = {"account_id": setup.account.id, "folder": "INBOX", "match_field": "from", "match_value": "boss@",
                "action": "add_note", "endpoint_id": "", "model": ""}
    return setup.rules.add(**{**defaults, **fields, "last_seen_uid": fields.get("last_seen_uid", 3)})


def actions(mailbox):
    return [call[0] for call in mailbox.calls if call[0] != "list_messages"]


@pytest.mark.parametrize("action, client_call", [
    ("mark_read", "mark_read"), ("archive", "archive_message"), ("delete", "delete_message"),
])
def test_a_rule_acts_only_on_new_matching_mail(setup, mailbox, action, client_call):
    rule = add_rule(setup, action=action)
    asyncio.run(setup.checker._tick())
    assert [c for c in mailbox.calls if c[0] == client_call] == [(client_call, "me@work.example", "mail-pass", "INBOX", "7")]
    assert setup.rules.get(rule.id).last_seen_uid == 8


def test_mail_already_seen_is_never_acted_on_twice(setup, mailbox):
    rule = add_rule(setup, action="delete")
    asyncio.run(setup.checker._tick())
    asyncio.run(setup.checker._tick())
    assert actions(mailbox) == ["delete_message"]
    assert setup.rules.get(rule.id).last_seen_uid == 8


def test_a_subject_rule_matches_the_subject_without_case(setup, mailbox):
    add_rule(setup, match_field="subject", match_value="BOSS FIGHT", action="archive")
    asyncio.run(setup.checker._tick())
    assert [(c[0], c[-1]) for c in mailbox.calls if c[0] != "list_messages"] == [("archive_message", "8")]


def test_an_add_note_rule_writes_a_note_and_touches_no_mail(setup, mailbox):
    add_rule(setup, action="add_note")
    asyncio.run(setup.checker._tick())
    assert actions(mailbox) == []
    [note] = setup.notes.list()
    assert note.title == "Email: Quarterly numbers" and "BOSS@work.example" in note.content


def test_a_summary_rule_asks_its_model_and_leaves_the_mail_unread(setup, mailbox):
    add_rule(setup, action="ai_summarize_note", endpoint_id="ep-1", model="qwen3:14b")
    asyncio.run(setup.checker._tick())
    [read] = [c for c in mailbox.calls if c[0] == "read_message"]
    assert read[-2:] == ("7", False)
    [(endpoint_id, model, prompt)] = setup.prompts
    assert (endpoint_id, model) == ("ep-1", "qwen3:14b") and "Numbers are up." in prompt
    assert [(n.title, n.content) for n in setup.notes.list()] == [("Email: Quarterly numbers", "Short summary.")]


def test_a_disabled_rule_does_nothing(setup, mailbox):
    rule = add_rule(setup, action="delete")
    setup.rules.update(rule.id, enabled=False)
    asyncio.run(setup.checker._tick())
    assert mailbox.calls == []


def test_a_rule_with_nothing_to_match_never_matches(setup, mailbox):
    add_rule(setup, action="delete", match_value="")
    asyncio.run(setup.checker._tick())
    assert actions(mailbox) == []


def test_an_unknown_action_or_field_becomes_the_harmless_default(setup, mailbox):
    rule = add_rule(setup, action="forward_everything", match_field="body")
    assert (rule.action, rule.match_field) == ("add_note", "from")
    asyncio.run(setup.checker._tick())
    assert actions(mailbox) == [] and len(setup.notes.list()) == 1


def test_a_rule_with_no_password_on_file_does_not_connect(setup, mailbox):
    add_rule(setup, action="delete")
    setup.passwords.clear()
    asyncio.run(setup.checker._tick())
    assert mailbox.calls == []


def test_a_mailbox_that_cannot_be_read_leaves_the_rule_where_it_was(setup, mailbox, caplog):
    rule = add_rule(setup, action="delete")
    mailbox.state["fail"].add("list_messages")
    asyncio.run(setup.checker._tick())
    assert setup.rules.get(rule.id).last_seen_uid == 3
    assert "could not list messages" in caplog.text


def test_a_failing_action_is_logged_and_the_rule_still_moves_on(setup, mailbox, caplog):
    rule = add_rule(setup, action="archive")
    mailbox.state["fail"].add("archive_message")
    asyncio.run(setup.checker._tick())
    assert setup.rules.get(rule.id).last_seen_uid == 8
    assert "archive failed" in caplog.text


def test_each_account_only_runs_its_own_rules_against_its_own_mailboxes(tmp_path, mailbox):
    registry = user_data.configure(str(tmp_path))
    with user_data.run_as(ALICE):
        alice_account = user_data.PerUser("email_accounts").add("A", "alice@mail.example", "imap", 993, "smtp", 587)
        user_data.PerUser("email_rules").add(account_id=alice_account.id, match_value="boss@", action="mark_read")
    with user_data.run_as(BOB):
        user_data.PerUser("email_rules").add(account_id=alice_account.id, match_value="boss@", action="delete")
    passwords = {alice_account.id: "alice-pass"}
    checker = EmailRuleChecker(user_data.PerUser("email_rules"), user_data.PerUser("email_accounts"),
                               passwords.get, None, user_data.PerUser("notes"), user_ids_fn=registry.user_ids)
    asyncio.run(checker._tick())
    assert actions(mailbox) == ["mark_read", "mark_read"]
    assert {c[1] for c in mailbox.calls} == {"alice@mail.example"}


def test_the_checker_loop_survives_a_failing_tick_and_stops_when_asked(setup, monkeypatch, caplog):
    monkeypatch.setattr(email_rule_checker, "TICK_SECONDS", 0.01)
    ticks = []

    async def tick():
        ticks.append(1)
        if len(ticks) == 1:
            raise OSError("first check fails")
        setup.checker.stop()

    setup.checker._tick = tick
    asyncio.run(asyncio.wait_for(setup.checker.run_loop(), timeout=5))
    assert len(ticks) == 2 and "first check fails" in caplog.text
