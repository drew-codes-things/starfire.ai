import json
import os
import sqlite3
import subprocess
import sys

import bcrypt
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import account_creation
from auth import UserStore
from chat_session_store import ChatSessionStore
from model_access import ModelAccessStore
from note_store import NoteStore

PASSWORD = "correct horse battery staple"


def fast_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()


def run_script(script: str, database, data_dir, cwd):
    return subprocess.run([sys.executable, os.path.join(ROOT, script), str(database)],
                          env={**os.environ, "DATA_DIR": str(data_dir)}, cwd=cwd,
                          capture_output=True, text=True)


def admin_dir(data_dir) -> str:
    user = UserStore(str(data_dir)).upsert("admin@example.com", "Admin", "admin", fast_hash(PASSWORD))
    return os.path.join(str(data_dir), "users", user.id)


def test_import_copies_open_webui_accounts(tmp_path):
    database = tmp_path / "webui.db"
    connection = sqlite3.connect(database)
    connection.executescript("""
        create table user (id text, name text, email text, role text);
        create table auth (id text, email text, password text, active integer);
    """)
    rows = [("1", "Drew", "drew@example.com", "admin", 1), ("2", "Waiting", "w@example.com", "pending", 1),
            ("3", "Gone", "g@example.com", "user", 0)]
    for user_id, name, email, role, active in rows:
        connection.execute("insert into user values (?, ?, ?, ?)", (user_id, name, email, role))
        connection.execute("insert into auth values (?, ?, ?, ?)", (user_id, email, fast_hash(PASSWORD), active))
    connection.commit()
    connection.close()

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    result = run_script("import_openwebui_users.py", database, data_dir, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "imported 1 account(s): Drew (admin)" in result.stdout
    assert "Waiting (role pending)" in result.stdout
    assert PASSWORD not in result.stdout and "$2b$" not in result.stdout
    imported = UserStore(str(data_dir))
    assert [u.email for u in imported.list()] == ["drew@example.com"]
    assert imported.verify("drew@example.com", PASSWORD)


def test_import_copies_open_webui_chats_and_notes_once(tmp_path):
    database = tmp_path / "webui.db"
    connection = sqlite3.connect(database)
    connection.executescript("""
        create table chat (id text, title text, chat text, created_at integer, updated_at integer,
                           archived integer, pinned integer);
        create table note (id text, title text, data text);
    """)
    chat = {"messages": [{"role": "user", "content": "hello"},
                         {"role": "assistant", "content": "hi", "model": "arty"},
                         {"role": "system", "content": "ignored"}], "tags": ["misc"]}
    connection.execute("insert into chat values (?, ?, ?, ?, ?, ?, ?)",
                       ("c1", "First chat", json.dumps(chat), 1761000000, 1761000100, 0, 1))
    connection.execute("insert into note values (?, ?, ?)",
                       ("n1", "Guide", json.dumps({"content": {"md": "# Guide", "html": "", "json": None}})))
    connection.commit()
    connection.close()

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    missing = run_script("import_openwebui_data.py", database, data_dir, tmp_path)
    assert missing.returncode == 1 and "no starfire account" in missing.stderr
    user_dir = admin_dir(data_dir)
    first = run_script("import_openwebui_data.py", database, data_dir, tmp_path)
    assert first.returncode == 0, first.stderr
    assert "into admin@example.com's account" in first.stdout
    assert "chats: 1 copied ['First chat']" in first.stdout
    second = run_script("import_openwebui_data.py", database, data_dir, tmp_path)
    assert "chats: 0 copied" in second.stdout and "notes: 0 copied" in second.stdout

    [session] = ChatSessionStore(user_dir).list()
    assert session.title == "First chat" and session.pinned
    assert [(m["role"], m["content"]) for m in session.messages] == [("user", "hello"), ("assistant", "hi")]
    assert session.messages[1]["model"] == "arty"
    assert set(session.tags) == {"misc", "open-webui"}
    assert session.created.startswith("2025-10-20")
    [note] = NoteStore(user_dir).list()
    assert (note.title, note.content) == ("Guide", "# Guide")


def test_import_follows_open_webui_history_thread(tmp_path):
    database = tmp_path / "webui.db"
    connection = sqlite3.connect(database)
    connection.executescript("""
        create table chat (id text, title text, chat text, created_at integer, updated_at integer,
                           archived integer, pinned integer);
        create table note (id text, title text, data text);
    """)
    history = {"currentId": "a2", "messages": {
        "u1": {"id": "u1", "parentId": None, "role": "user", "content": "first"},
        "a1": {"id": "a1", "parentId": "u1", "role": "assistant", "content": "reply one", "model": "arty"},
        "old": {"id": "old", "parentId": "u1", "role": "assistant", "content": "abandoned branch"},
        "u2": {"id": "u2", "parentId": "a1", "role": "user", "content": "second"},
        "a2": {"id": "a2", "parentId": "u2", "role": "assistant", "content": ""}}}
    chat = {"messages": [{"role": "user", "content": "first"}], "history": history}
    connection.execute("insert into chat values (?, ?, ?, ?, ?, ?, ?)",
                       ("c1", "Thread", json.dumps(chat), 1761000000, 1761000100, 0, 0))
    connection.commit()
    connection.close()

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    user_dir = admin_dir(data_dir)
    result = run_script("import_openwebui_data.py", database, data_dir, tmp_path)
    assert result.returncode == 0, result.stderr

    [session] = ChatSessionStore(user_dir).list()
    assert [(m["role"], m["content"]) for m in session.messages] == [
        ("user", "first"), ("assistant", "reply one"), ("user", "second")]


def openwebui_database(path, accounts):
    connection = sqlite3.connect(path)
    connection.executescript("""
        create table user (id text, name text, email text, role text);
        create table auth (id text, email text, password text, active integer);
    """)
    for number, (name, email, role) in enumerate(accounts, 1):
        connection.execute("insert into user values (?, ?, ?, ?)", (str(number), name, email, role))
        connection.execute("insert into auth values (?, ?, ?, ?)", (str(number), email, fast_hash(PASSWORD), 1))
    connection.commit()
    connection.close()
    return path


def test_imported_new_accounts_get_the_default_models_and_existing_ones_keep_theirs(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    store = UserStore(str(data_dir))
    kept = store.upsert("kim@example.com", "Kim", "user", fast_hash(PASSWORD))
    access = ModelAccessStore(str(data_dir))
    access.set_allowed(kept.id, ["llama3.1:8b"])
    access.set_default_for_new_accounts(["gpt-4o"])
    database = openwebui_database(tmp_path / "webui.db", [("Sam", "sam@example.com", "user"),
                                                          ("Kim", "kim@example.com", "user")])
    result = run_script("import_openwebui_users.py", database, data_dir, tmp_path)
    assert result.returncode == 0, result.stderr
    sam = store.find_by_email("sam@example.com")
    assert access.allowed_for(sam.id) == ["gpt-4o"]
    assert access.allowed_for(kept.id) == ["llama3.1:8b"]


def test_an_account_is_rolled_back_when_its_default_models_cannot_be_saved(tmp_path, monkeypatch):
    store = UserStore(str(tmp_path))
    store.upsert("admin@example.com", "Admin", "admin", fast_hash(PASSWORD))
    ModelAccessStore(str(tmp_path)).set_default_for_new_accounts(["gpt-4o"])

    def broken_disk(self, user_id, patterns):
        raise OSError("disk full")

    monkeypatch.setattr(ModelAccessStore, "set_allowed", broken_disk)
    with pytest.raises(OSError):
        account_creation.create_account(store, str(tmp_path), "sam@example.com", "Sam", PASSWORD, "user")
    with pytest.raises(OSError):
        account_creation.import_account(store, str(tmp_path), "kim@example.com", "Kim", "user", fast_hash(PASSWORD))
    assert [u.email for u in store.list()] == ["admin@example.com"]
