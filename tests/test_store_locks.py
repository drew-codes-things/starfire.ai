import asyncio
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import pytest

import atomic_io
from api_key_manager import APIKeyManager
from chat_session_store import ChatSessionStore
from documents_store import DocumentStore
from email_store import EmailAccountStore, EmailRuleStore
from folder_store import FolderStore
from local_gen_store import ComfyUIConfigStore, CustomWorkflowStore, GeneratedFileStore
from mcp_servers_store import McpServerStore
from memory_store import MemoryStore
from model_endpoints import ModelEndpointStore
from note_store import NoteStore, NoteTemplateStore
from preset_store import PresetStore
from prompt_store import PromptStore
from task_store import ScheduledTask, TaskRun, TaskRunStore, TaskStore
from usage_store import UsageStore

WRITERS = 12
SLOW_READ_SECONDS = 0.02


def raw_reads_of(store) -> str:
    for name in ("_read_items", "_load_raw", "_load"):
        if hasattr(store, name):
            return name
    raise AssertionError(f"{type(store).__name__} has no loader to slow down")


STORES = [
    ("chat sessions", ChatSessionStore, lambda s, i: s.add(title=f"chat {i}"), lambda s: len(s.all())),
    ("folders", FolderStore, lambda s, i: s.add(f"folder {i}"), lambda s: len(s.list())),
    ("memory", MemoryStore, lambda s, i: s.add(f"fact number {i}"), lambda s: len(s.list())),
    ("documents", DocumentStore, lambda s, i: s.add(f"doc{i}.txt", f"document {i} text.".encode()),
     lambda s: len(s.list())),
    ("notes", NoteStore, lambda s, i: s.add(title=f"note {i}"), lambda s: len(s.list())),
    ("note templates", NoteTemplateStore, lambda s, i: s.add(name=f"template {i}"), lambda s: len(s.list())),
    ("tasks", TaskStore, lambda s, i: s.add(ScheduledTask(id=f"t{i}", name=f"task {i}", prompt="p",
                                                          schedule="daily")), lambda s: len(s.list())),
    ("task runs", TaskRunStore, lambda s, i: s.add(TaskRun(id=f"r{i}", task_id="t", started="now")),
     lambda s: len(s.recent(limit=1000))),
    ("email accounts", EmailAccountStore,
     lambda s, i: s.add(f"box {i}", f"user{i}@example.test", "imap.example.test", 993, "smtp.example.test", 465),
     lambda s: len(s.list())),
    ("email rules", EmailRuleStore,
     lambda s, i: s.add(account_id="a", match_field="from", match_value=f"x{i}", action="add_note"),
     lambda s: len(s.list())),
    ("presets", PresetStore, lambda s, i: s.add(name=f"preset {i}"), lambda s: len(s.list())),
    ("prompts", PromptStore, lambda s, i: s.add(f"cmd{i}", f"prompt {i}", "content"), lambda s: len(s.list())),
    ("usage", UsageStore, lambda s, i: s.record("openai", "m", "in", "out"),
     lambda s: s.summary()["all_time"]["turns"]),
    ("model endpoints", ModelEndpointStore, lambda s, i: s.add(f"http://10.0.0.{i}:11434", kind="ollama"),
     lambda s: len(s.list())),
    ("custom workflows", CustomWorkflowStore, lambda s, i: s.add(f"wf {i}", {"1": {}}, "1"),
     lambda s: len(s.list())),
    ("generated files", GeneratedFileStore, lambda s, i: s.add("image", f"f{i}.png", "image/png", b"x"),
     lambda s: len(s.list())),
    ("mcp servers", McpServerStore, lambda s, i: s.add(name=f"server {i}", command="npx", args=[str(i)],
                                                       env={"TOKEN": f"PLACEHOLDER-{i}"}),
     lambda s: len(s.list())),
    ("api keys", APIKeyManager, lambda s, i: s.save(f"endpoint-{i}", f"PLACEHOLDER-{i}"),
     lambda s: len(s.load())),
]


def slow_down_reads(store, monkeypatch):
    name = raw_reads_of(store)
    real_read = getattr(store, name)

    def slow_read(*args, **kwargs):
        result = real_read(*args, **kwargs)
        time.sleep(SLOW_READ_SECONDS)
        return result

    monkeypatch.setattr(store, name, slow_read)


def run_together(count: int, action) -> list[BaseException]:
    start = threading.Barrier(count)
    failures: list[BaseException] = []

    def worker(index: int):
        start.wait()
        try:
            action(index)
        except BaseException as error:
            failures.append(error)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return failures


@pytest.mark.parametrize("label, factory, add_one, count", STORES, ids=[s[0] for s in STORES])
def test_concurrent_additions_to_one_store_are_all_kept(tmp_path, monkeypatch, label, factory, add_one, count):
    store = factory(str(tmp_path))
    if isinstance(store, NoteTemplateStore):
        assert store.list() == []
    slow_down_reads(store, monkeypatch)
    failures = run_together(WRITERS, lambda i: add_one(store, i))
    assert failures == []
    assert count(factory(str(tmp_path))) == WRITERS


def test_concurrent_changes_to_one_settings_file_are_all_kept(tmp_path, monkeypatch):
    store = ComfyUIConfigStore(str(tmp_path))
    real_get = store.get

    def slow_get():
        current = real_get()
        time.sleep(SLOW_READ_SECONDS)
        return current

    monkeypatch.setattr(store, "get", slow_get)
    fields = ["base_url", "checkpoints_dir", "default_checkpoint", "default_negative_prompt"]
    failures = run_together(len(fields), lambda i: store.update(**{fields[i]: f"value-{i}"}))
    assert failures == []
    saved = ComfyUIConfigStore(str(tmp_path)).get()
    assert [getattr(saved, name) for name in fields] == [f"value-{i}" for i in range(len(fields))]


def test_two_store_objects_for_the_same_file_share_one_lock(tmp_path):
    first = atomic_io.lock_for(str(tmp_path / "notes.json"))
    second = atomic_io.lock_for(str(tmp_path / "." / "notes.json"))
    assert first is second
    assert atomic_io.lock_for(str(tmp_path / "memory.json")) is not first


def fake_ollama(monkeypatch, module, while_embedding):
    async def detect(_url):
        return "http://ollama.example.test:11434"

    async def embed(text, _url):
        await while_embedding()
        return [1.0, float(len(text))]

    monkeypatch.setattr(module, "detect_ollama", detect)
    monkeypatch.setattr(module.embeddings_module, "embed", embed)


def test_a_document_added_while_search_embeds_chunks_is_kept(tmp_path, monkeypatch):
    import documents_store

    store = DocumentStore(str(tmp_path))
    store.add("first.txt", b"The first document talks about boats.")
    added = []

    async def add_another():
        if not added:
            added.append(store.add("second.txt", b"The second document talks about trains."))

    fake_ollama(monkeypatch, documents_store, add_another)
    asyncio.run(store.search("boats"))
    names = sorted(d.filename for d in DocumentStore(str(tmp_path)).list())
    assert names == ["first.txt", "second.txt"]
    stored = DocumentStore(str(tmp_path))._load_raw()["chunks"]
    first_chunks = [c for c in stored if c["document_id"] != added[0].id]
    assert first_chunks and all(c.get("embedding") for c in first_chunks)


def test_a_memory_added_while_relevant_embeds_entries_is_kept(tmp_path, monkeypatch):
    import memory_store

    store = MemoryStore(str(tmp_path))
    store.add("Drew likes sailing boats")
    added = []

    async def add_another():
        if not added:
            added.append(store.add("The cat is called Pixel"))

    fake_ollama(monkeypatch, memory_store, add_another)
    asyncio.run(store.relevant("boats"))
    texts = sorted(e.text for e in MemoryStore(str(tmp_path)).list())
    assert texts == ["Drew likes sailing boats", "The cat is called Pixel"]
    [sailing] = [e for e in MemoryStore(str(tmp_path)).list() if e.text.startswith("Drew")]
    assert sailing.embedding
