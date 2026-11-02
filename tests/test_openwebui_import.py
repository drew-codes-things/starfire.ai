import json
import os
import sqlite3
import subprocess
import sys

import bcrypt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

from auth import UserStore
from chat_session_store import ChatSessionStore
from note_store import NoteStore

PASSWORD = "correct horse battery staple"


def fast_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()


def run_script(script: str, database, data_dir, cwd):
    return subprocess.run([sys.executable, os.path.join(ROOT, script), str(database)],
                          env={**os.environ, "DATA_DIR": str(data_dir)}, cwd=cwd,
                          capture_output=True, text=True)


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
    first = run_script("import_openwebui_data.py", database, data_dir, tmp_path)
    assert first.returncode == 0, first.stderr
    assert "chats: 1 copied ['First chat']" in first.stdout
    second = run_script("import_openwebui_data.py", database, data_dir, tmp_path)
    assert "chats: 0 copied" in second.stdout and "notes: 0 copied" in second.stdout

    [session] = ChatSessionStore(str(data_dir)).list()
    assert session.title == "First chat" and session.pinned
    assert [(m["role"], m["content"]) for m in session.messages] == [("user", "hello"), ("assistant", "hi")]
    assert session.messages[1]["model"] == "arty"
    assert set(session.tags) == {"misc", "open-webui"}
    assert session.created.startswith("2025-10-20")
    [note] = NoteStore(str(data_dir)).list()
    assert (note.title, note.content) == ("Guide", "# Guide")
