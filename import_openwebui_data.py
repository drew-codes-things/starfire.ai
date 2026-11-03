import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_configs"))

from dotenv import load_dotenv

load_dotenv()

import user_data
from atomic_io import atomic_write_json
from auth import UserStore
from chat_session_store import ChatSessionStore
from config import config
from note_store import NoteStore

IMPORTED_FILE = "openwebui_imported.json"
CHAT_ROLES = ("user", "assistant")


def iso_time(seconds: float | int | None) -> str:
    if not seconds:
        return datetime.now(timezone.utc).isoformat()
    if seconds > 1e12:
        seconds = seconds / 1e9
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def history_thread(chat: dict) -> list[dict]:
    history = chat.get("history") or {}
    by_id = history.get("messages") or {}
    thread, seen = [], set()
    message_id = history.get("currentId")
    while message_id in by_id and message_id not in seen:
        seen.add(message_id)
        thread.append(by_id[message_id])
        message_id = by_id[message_id].get("parentId")
    return thread[::-1]


def chat_messages(chat: dict, created: str) -> list[dict]:
    messages = []
    for message in history_thread(chat) or chat.get("messages") or []:
        content = message.get("content")
        if message.get("role") not in CHAT_ROLES or not isinstance(content, str) or not content.strip():
            continue
        entry = {"role": message["role"], "content": content,
                 "created": iso_time(message.get("timestamp")) if message.get("timestamp") else created}
        if message["role"] == "assistant" and message.get("model"):
            entry["model"] = message["model"]
        messages.append(entry)
    return messages


def note_text(data: str | None) -> str:
    content = (json.loads(data or "{}").get("content") or {})
    return content.get("md") or ""


def read_rows(database_path: str, query: str) -> list[tuple]:
    if not os.path.isfile(database_path):
        raise FileNotFoundError(f"no Open WebUI database at {database_path}")
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    try:
        return connection.execute(query).fetchall()
    finally:
        connection.close()


def load_imported(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        return set(json.load(f))


def import_chats(database_path: str, chats: ChatSessionStore, imported: set[str]) -> list[str]:
    rows = read_rows(database_path, "select id, title, chat, created_at, updated_at, archived, pinned from chat")
    items, titles = [], []
    for chat_id, title, raw_chat, created_at, updated_at, archived, pinned in rows:
        key = f"chat:{chat_id}"
        if key in imported:
            continue
        chat = json.loads(raw_chat or "{}")
        created = iso_time(created_at)
        items.append({"title": title or chat.get("title") or "Imported chat",
                      "messages": chat_messages(chat, created), "archived": bool(archived),
                      "pinned": bool(pinned), "tags": [*(chat.get("tags") or []), "open-webui"],
                      "created": created, "updated": iso_time(updated_at)})
        titles.append(items[-1]["title"])
        imported.add(key)
    if items:
        chats.add_many(items)
    return titles


def import_notes(database_path: str, notes: NoteStore, imported: set[str]) -> list[str]:
    titles = []
    for note_id, title, data in read_rows(database_path, "select id, title, data from note"):
        key = f"note:{note_id}"
        if key in imported:
            continue
        notes.add(title=title or "Imported note", content=note_text(data), source="open-webui")
        titles.append(title)
        imported.add(key)
    return titles


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy Open WebUI chats and notes into starfire.ai. "
                                                 "Running it again only copies ones it hasn't copied before.")
    parser.add_argument("database", help="path to Open WebUI's webui.db")
    parser.add_argument("--user", help="email of the starfire account to import into (default: the first admin)")
    args = parser.parse_args()

    users = UserStore(config.data_dir)
    account = users.find_by_email(args.user) if args.user else users.first_admin()
    if not account:
        print("no starfire account to import into - set one up first (or pass --user EMAIL)", file=sys.stderr)
        return 1
    registry = user_data.configure(config.data_dir)
    user_dir = registry.dir_for(account.id)
    os.makedirs(user_dir, exist_ok=True)

    imported_path = os.path.join(user_dir, IMPORTED_FILE)
    imported = load_imported(imported_path)
    chat_titles = import_chats(args.database, ChatSessionStore(user_dir), imported)
    note_titles = import_notes(args.database, NoteStore(user_dir), imported)
    atomic_write_json(imported_path, sorted(imported))

    print(f"into {account.email}'s account")
    print(f"chats: {len(chat_titles)} copied {chat_titles}")
    print(f"notes: {len(note_titles)} copied {note_titles}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
