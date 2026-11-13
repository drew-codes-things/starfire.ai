import asyncio
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import agent_loop
import builtin_tools
import email_client
import file_edit_tool
import memory_store
from api_key_manager import APIKeyManager
from builtin_tools import ToolContext
from documents_store import DocumentStore
from email_store import EmailAccountStore
from local_gen_store import GeneratedFileStore
from memory_store import MemoryStore
from note_store import NoteStore
from task_store import TaskRunStore, TaskStore


@pytest.fixture
def ctx(tmp_path):
    data_dir = str(tmp_path)
    return ToolContext(
        memory=MemoryStore(data_dir), documents=DocumentStore(data_dir), tasks=TaskStore(data_dir),
        task_runs=TaskRunStore(data_dir), email_accounts=EmailAccountStore(data_dir),
        api_keys=APIKeyManager(data_dir), notes=NoteStore(data_dir), generated_files=GeneratedFileStore(data_dir),
        base_url="http://127.0.0.1:11434", model="qwen3:14b", endpoint_id="ep-1",
    )


def call(name, arguments, ctx):
    return asyncio.run(builtin_tools.call_builtin_tool(name, arguments, ctx))


@pytest.fixture
def mailbox(monkeypatch):
    calls = []

    def record(name, result):
        def recorded(account, password, *args, **kwargs):
            calls.append((name, account.email_address, password, *args))
            return result
        return recorded

    monkeypatch.setattr(email_client, "list_messages", record("list_messages", [{"uid": "1"}]))
    monkeypatch.setattr(email_client, "send_message", record("send_message", None))
    monkeypatch.setattr(email_client, "delete_message", record("delete_message", None))
    return calls


@pytest.fixture
def account(ctx):
    created = ctx.email_accounts.add("Work", "me@work.example", "imap.work.example", 993, "smtp.work.example", 587)
    ctx.api_keys.save(created.id, "mail-pass")
    return created


@pytest.mark.parametrize("account_id", [None, "", "someone-elses-account"])
def test_the_model_cannot_use_a_mailbox_that_is_not_this_accounts(ctx, account, mailbox, account_id):
    arguments = {"action": "delete", "uid": "1"} | ({"account_id": account_id} if account_id is not None else {})
    assert call("manage_email", arguments, ctx) == "error: a valid account_id is required"
    assert mailbox == []


def test_the_model_reads_mail_with_the_stored_password_and_never_sees_it(ctx, account, mailbox):
    listed = call("manage_email", {"action": "list_accounts"}, ctx)
    assert json.loads(listed) == [{"id": account.id, "label": "Work", "email_address": "me@work.example"}]
    assert "mail-pass" not in listed
    assert json.loads(call("manage_email", {"action": "list_messages", "account_id": account.id}, ctx)) == [{"uid": "1"}]
    assert mailbox == [("list_messages", "me@work.example", "mail-pass", "INBOX")]


@pytest.mark.parametrize("arguments, expected", [
    ({"action": "send", "to": "", "body": "hi"}, "error: to and body are required"),
    ({"action": "delete"}, "error: uid is required"),
    ({"action": "search"}, "error: query is required"),
    ({"action": "forward_all"}, "error: unknown action 'forward_all'"),
])
def test_incomplete_mail_actions_are_refused_without_touching_the_mailbox(ctx, account, mailbox, arguments, expected):
    assert call("manage_email", {**arguments, "account_id": account.id}, ctx) == expected
    assert mailbox == []


def test_a_mail_server_error_comes_back_as_a_tool_error(ctx, account, monkeypatch):
    def refuse(*args):
        raise OSError("login refused")

    monkeypatch.setattr(email_client, "send_message", refuse)
    result = call("manage_email", {"action": "send", "account_id": account.id, "to": "a@b.example", "body": "x"}, ctx)
    assert result == "error: login refused"


def test_the_model_manages_tasks_on_its_own_endpoint_and_model(ctx):
    created = json.loads(call("manage_tasks", {"action": "create", "prompt": "check the weather",
                                               "schedule": "daily", "scheduled_time": "07:30"}, ctx))
    stored = ctx.tasks.get(created["id"])
    assert (stored.endpoint_id, stored.model, stored.name) == ("ep-1", "qwen3:14b", "check the weather")
    assert call("manage_tasks", {"action": "pause", "task_id": created["id"]}, ctx) == "paused"
    assert ctx.tasks.get(created["id"]).status == "paused"
    assert call("manage_tasks", {"action": "resume", "task_id": created["id"]}, ctx) == "resumed"
    assert [t["status"] for t in json.loads(call("manage_tasks", {"action": "list"}, ctx))] == ["active"]
    assert call("manage_tasks", {"action": "delete", "task_id": created["id"]}, ctx) == "deleted"
    assert ctx.tasks.list() == []


@pytest.mark.parametrize("arguments, expected_start", [
    ({"action": "create", "prompt": "x", "schedule": "hourly"}, "error: prompt and a valid schedule"),
    ({"action": "create", "schedule": "daily"}, "error: prompt and a valid schedule"),
    ({"action": "create", "prompt": "x", "schedule": "cron", "cron_expression": "nonsense"}, "error:"),
    ({"action": "pause"}, "error: task_id is required"),
    ({"action": "delete", "task_id": "missing"}, "error: no task with id missing"),
    ({"action": "explode"}, "error: unknown action 'explode'"),
])
def test_bad_task_requests_from_the_model_change_nothing(ctx, arguments, expected_start):
    assert call("manage_tasks", arguments, ctx).startswith(expected_start)
    assert ctx.tasks.list() == []


def test_running_a_task_now_records_the_models_answer(ctx, monkeypatch):
    async def collected(base_url, api_key, model, messages, **kwargs):
        return f"{model} says: {messages[0]['content']}"

    monkeypatch.setattr(agent_loop, "run_chat_collected", collected)
    task_id = json.loads(call("manage_tasks", {"action": "create", "prompt": "ping", "schedule": "once"}, ctx))["id"]
    assert call("manage_tasks", {"action": "run_now", "task_id": task_id}, ctx) == "ran"
    assert [(r.status, r.output) for r in ctx.task_runs.for_task(task_id)] == [("ok", "qwen3:14b says: ping")]
    assert ctx.tasks.get(task_id).status == "paused"


def test_the_model_keeps_notes_and_checklists(ctx):
    note = json.loads(call("manage_notes", {"action": "add", "title": "Shopping", "note_type": "checklist",
                                            "checklist_items": [{"text": "milk"}, {"text": "eggs"}]}, ctx))
    assert call("manage_notes", {"action": "toggle_item", "note_id": note["id"], "item_index": 1}, ctx) == "toggled"
    assert call("manage_notes", {"action": "toggle_item", "note_id": note["id"], "item_index": 9}, ctx).startswith("error")
    assert call("manage_notes", {"action": "update", "note_id": note["id"], "title": "Groceries"}, ctx) == "updated"
    viewed = json.loads(call("manage_notes", {"action": "view", "note_id": note["id"]}, ctx))
    assert viewed["title"] == "Groceries" and [i["done"] for i in viewed["items"]] == [False, True]
    assert [n["id"] for n in json.loads(call("manage_notes", {"action": "list"}, ctx))] == [note["id"]]
    assert call("manage_notes", {"action": "delete", "note_id": note["id"]}, ctx) == "deleted"
    assert call("manage_notes", {"action": "view", "note_id": note["id"]}, ctx) == f"error: no note with id {note['id']}"


@pytest.mark.parametrize("arguments", [
    {"action": "add"}, {"action": "view"}, {"action": "update"}, {"action": "delete"},
    {"action": "toggle_item", "note_id": "x"}, {"action": "update", "note_id": "missing", "title": "x"},
    {"action": "delete", "note_id": "missing"}, {"action": "burn"},
])
def test_bad_note_requests_from_the_model_are_errors(ctx, arguments):
    assert call("manage_notes", arguments, ctx).startswith("error")
    assert ctx.notes.list() == []


def test_the_model_manages_memory(ctx, monkeypatch):
    async def no_ollama(url):
        return None

    monkeypatch.setattr(memory_store, "detect_ollama", no_ollama)
    added = json.loads(call("manage_memory", {"action": "add", "text": "Drew's dog is called Pixel"}, ctx))
    assert call("manage_memory", {"action": "edit", "memory_id": added["id"], "text": "The dog is Pixel"}, ctx) == "updated"
    found = json.loads(call("manage_memory", {"action": "search", "query": "dog Pixel"}, ctx))
    assert [m["text"] for m in found] == ["The dog is Pixel"]
    assert call("manage_memory", {"action": "delete", "memory_id": added["id"]}, ctx) == "deleted"
    assert json.loads(call("manage_memory", {"action": "list"}, ctx)) == []


@pytest.mark.parametrize("arguments", [
    {"action": "add"}, {"action": "edit"}, {"action": "delete"},
    {"action": "edit", "memory_id": "missing", "text": "x"}, {"action": "delete", "memory_id": "missing"},
    {"action": "forget_everything"},
])
def test_bad_memory_requests_from_the_model_are_errors(ctx, arguments):
    assert call("manage_memory", arguments, ctx).startswith("error")


def test_a_generated_document_gets_a_safe_name_and_its_content(ctx):
    result = json.loads(call("generate_document", {"content": "# Plan\n\nStep one.", "format": "md",
                                                   "filename": "../../etc/passwd"}, ctx))
    entry = ctx.generated_files.get(result["id"])
    assert entry.filename == ".._.._etc_passwd.md" and result["url"] == f"/api/generated/{entry.id}"
    path = ctx.generated_files.path_for(entry)
    assert os.path.dirname(path) == ctx.generated_files.files_dir
    with open(path, encoding="utf-8") as handle:
        assert "Step one." in handle.read()


@pytest.mark.parametrize("arguments, expected", [
    ({"format": "md"}, "error: content is required"),
    ({"content": "x", "format": "exe"}, "error: unsupported format 'exe'"),
])
def test_bad_document_requests_make_no_file(ctx, arguments, expected):
    assert call("generate_document", arguments, ctx) == expected
    assert ctx.generated_files.list() == []


def test_an_edit_that_needs_approval_is_staged_not_written(ctx, tmp_path):
    ctx.may_use_host_tools = True
    ctx.require_edit_approval = True
    target = tmp_path / "config.txt"
    target.write_text("old\n")
    staged = json.loads(call("edit_file", {"path": str(target), "content": "new\n"}, ctx))
    try:
        assert staged["staged"] is True and "-old" in staged["diff"] and "+new" in staged["diff"]
        assert target.read_text() == "old\n"
    finally:
        file_edit_tool.remove_pending(staged["pending_id"], None)


def test_an_edit_without_a_path_is_refused(ctx):
    ctx.may_use_host_tools = True
    assert call("edit_file", {"content": "x"}, ctx) == "error: path is required"


def test_media_tools_say_what_to_set_up_when_nothing_can_make_media(ctx):
    assert "no local ComfyUI" in call("generate_image", {"prompt": "a cat"}, ctx)
    assert "no local Piper voice" in call("generate_audio", {"text": "hello"}, ctx)
    assert call("generate_video", {"prompt": "a cat"}, ctx) == "error: workflow_name and prompt are required"
    assert call("generate_image", {}, ctx) == "error: prompt is required"
    assert ctx.generated_files.list() == []


def test_an_unknown_tool_is_an_error_not_a_silent_success(ctx):
    with pytest.raises(ValueError, match="unknown builtin tool"):
        call("format_disk", {}, ctx)


def test_only_the_enabled_tools_are_offered_to_the_model():
    offered = builtin_tools.schemas_for({"manage_notes", "run_shell", "not_a_tool"})
    assert sorted(s["function"]["name"] for s in offered) == ["manage_notes", "run_shell"]
    assert builtin_tools.is_builtin_tool("manage_notes") and not builtin_tools.is_builtin_tool("not_a_tool")


def test_tool_events_only_go_somewhere_when_the_chat_listens(ctx):
    asyncio.run(ctx.emit({"ignored": True}))
    ctx.events = asyncio.Queue()
    asyncio.run(ctx.emit({"heard": True}))
    assert ctx.events.get_nowait() == {"heard": True}
