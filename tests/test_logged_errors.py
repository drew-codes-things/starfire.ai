import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import httpx
import pytest

import api_key_manager
import atomic_io
import deep_research
import file_edit_tool
import local_gen_store
import providers
import task_scheduler
import web_search
from attachments import AttachmentStore
from chat_session_store import ChatSessionStore
from email_store import EmailAccountStore, EmailRuleStore
from folder_store import FolderStore
from local_gen_store import (ComfyUIConfigStore, CustomWorkflowStore, GeneratedFileStore,
                             PiperConfigStore)
from mcp_servers_store import McpServerStore
from memory_store import MemoryStore
from model_endpoints import ModelEndpointStore
from note_store import NoteStore, NoteTemplateStore
from preset_store import PresetStore
from prompt_store import PromptStore
from task_store import TaskRunStore, TaskStore
from usage_store import UsageStore

BROKEN = "{broken"
ATTACHMENT_ID = "ab" * 16

STORES = [
    (ChatSessionStore, "chat_sessions.json", lambda store: store.all()),
    (EmailAccountStore, "email_accounts.json", lambda store: store.list()),
    (EmailRuleStore, "email_rules.json", lambda store: store.list()),
    (FolderStore, "chat_folders.json", lambda store: store.list()),
    (ComfyUIConfigStore, "comfyui_config.json", lambda store: store.get()),
    (PiperConfigStore, "piper_config.json", lambda store: store.get()),
    (CustomWorkflowStore, "custom_workflows.json", lambda store: store.list()),
    (GeneratedFileStore, "generated_files.json", lambda store: store.list()),
    (McpServerStore, "mcp_servers.json", lambda store: store.list()),
    (MemoryStore, "memory.json", lambda store: store.list()),
    (ModelEndpointStore, "endpoints.json", lambda store: store.list()),
    (NoteStore, "notes.json", lambda store: store.list()),
    (NoteTemplateStore, "note_templates.json", lambda store: store.list()),
    (PresetStore, "presets.json", lambda store: store.list()),
    (PromptStore, "prompts.json", lambda store: store.list()),
    (TaskStore, "tasks.json", lambda store: store.list()),
    (TaskRunStore, "task_runs.json", lambda store: store.recent()),
    (UsageStore, "usage.json", lambda store: store.summary()),
]


def error_records(caplog, level=logging.WARNING):
    return [record for record in caplog.records if record.levelno >= level]


def refuse_to_remove(path):
    raise PermissionError(13, "Permission denied", path)


@pytest.mark.parametrize("store_class, filename, read", STORES, ids=[s[1] for s in STORES])
def test_a_store_file_that_is_not_json_is_logged_and_read_as_empty(tmp_path, caplog, store_class, filename, read):
    broken_dir, fresh_dir = tmp_path / "broken", tmp_path / "fresh"
    broken_dir.mkdir()
    fresh_dir.mkdir()
    (broken_dir / filename).write_text(BROKEN)
    assert read(store_class(str(broken_dir))) == read(store_class(str(fresh_dir)))
    [record] = error_records(caplog, logging.ERROR)
    assert filename in record.getMessage()
    assert BROKEN not in record.getMessage()


def test_a_scheduled_time_that_is_not_hours_and_minutes_is_logged_and_runs_at_nine(caplog):
    after = datetime(2026, 11, 13, 6, 0, tzinfo=timezone.utc)
    next_run = task_scheduler.compute_next_run("daily", scheduled_time="half past nine", after=after)
    assert next_run == datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc).isoformat()
    [record] = error_records(caplog)
    assert "half past nine" in record.getMessage()


def test_a_generated_file_that_cannot_be_removed_is_logged_and_still_leaves_the_list(tmp_path, monkeypatch, caplog):
    store = GeneratedFileStore(str(tmp_path))
    entry = store.add("image", "cat.png", "image/png", b"png")
    monkeypatch.setattr(local_gen_store.os, "remove", refuse_to_remove)
    assert store.delete(entry.id) is True
    assert store.list() == []
    [record] = error_records(caplog)
    assert store.path_for(entry) in record.getMessage()


def test_a_generated_file_already_gone_is_not_a_warning(tmp_path, caplog):
    store = GeneratedFileStore(str(tmp_path))
    entry = store.add("image", "cat.png", "image/png", b"png")
    os.remove(store.path_for(entry))
    assert store.delete(entry.id) is True
    assert error_records(caplog) == []


def test_a_file_that_exists_but_is_not_text_is_logged_before_an_edit_diff(tmp_path, caplog):
    binary = tmp_path / "logo.png"
    binary.write_bytes(b"\x89PNG\xff\xfe")
    assert file_edit_tool.read_current(str(binary)) == ""
    [record] = error_records(caplog)
    assert str(binary) in record.getMessage()


def test_a_new_file_for_an_edit_reads_as_empty_without_a_warning(tmp_path, caplog):
    assert file_edit_tool.read_current(str(tmp_path / "new.txt")) == ""
    assert error_records(caplog) == []


def test_a_temp_file_left_behind_by_a_failed_write_is_logged(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(atomic_io.os, "remove", refuse_to_remove)
    with pytest.raises(TypeError):
        atomic_io.atomic_write_json(str(tmp_path / "store.json"), {"not json": object()})
    [record] = error_records(caplog)
    assert str(tmp_path) in record.getMessage()


def test_a_key_file_whose_permissions_cannot_be_tightened_is_logged(tmp_path, monkeypatch, caplog):
    def refuse(path, mode):
        raise PermissionError(1, "Operation not permitted", path)

    monkeypatch.setattr(api_key_manager, "IS_WINDOWS", False)
    monkeypatch.setattr(api_key_manager.os, "chmod", refuse)
    assert api_key_manager.safe_chmod(str(tmp_path / ".key"), 0o600) is False
    [record] = error_records(caplog)
    assert ".key" in record.getMessage()


def test_an_attachment_that_cannot_be_read_is_logged_when_pruning(tmp_path, caplog):
    store = AttachmentStore(str(tmp_path))
    (tmp_path / "attachments" / f"{ATTACHMENT_ID}.json").write_text(BROKEN)
    assert store.prune() == 0
    [record] = error_records(caplog)
    assert ATTACHMENT_ID in record.getMessage()


def test_a_web_search_that_cannot_reach_the_search_site_is_logged(monkeypatch, caplog):
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(web_search.httpx, "AsyncClient",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    assert asyncio.run(web_search.search("private question")) == []
    [record] = error_records(caplog)
    assert "connection refused" in record.getMessage()
    assert "private question" not in record.getMessage()


def test_a_web_search_the_search_site_refuses_is_logged_without_the_query(monkeypatch, caplog):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(web_search.httpx, "AsyncClient",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(lambda request: httpx.Response(429)),
                                                     **kwargs))
    assert asyncio.run(web_search.search("private question")) == []
    [record] = error_records(caplog)
    assert "429" in record.getMessage()
    assert "private question" not in record.getMessage()


def test_page_text_read_before_markup_the_html_parser_rejects_is_kept(monkeypatch):
    real_feed = deep_research._TextExtractor.feed

    def feed_then_reject(extractor, html):
        real_feed(extractor, html)
        raise AssertionError("unknown status keyword 'bogus' in marked section")

    monkeypatch.setattr(deep_research._TextExtractor, "feed", feed_then_reject)
    assert deep_research._extract_text("<p>kept</p>") == "kept"


@pytest.mark.parametrize("base_url", ["http://[::1", 5])
def test_a_base_url_that_cannot_be_parsed_is_treated_as_openai(base_url):
    assert providers._detect_provider(base_url) == "openai"


def test_a_partial_download_is_cleaned_up_without_a_warning_when_already_gone(tmp_path, caplog):
    atomic_io.discard_temp_file(str(tmp_path / "gone.part"))
    assert error_records(caplog) == []
