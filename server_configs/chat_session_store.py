from __future__ import annotations

import copy
import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from atomic_io import atomic_write_json, holds_file_lock, report_unreadable_store

TAG_QUERY_PREFIX = "tag:"
MAX_TAG_LENGTH = 32

def normalize_tag(tag: str) -> str:
    return " ".join(str(tag).strip().lower().split())[:MAX_TAG_LENGTH]

@dataclass
class ChatSession:
    id: str
    title: str = "New chat"
    messages: list[dict] = field(default_factory=list)
    endpoint_id: str = ""
    model: str = ""
    pinned: bool = False
    parent_session_id: str = ""
    branch_point: int = -1
    folder_id: str = ""
    archived: bool = False
    tags: list[str] = field(default_factory=list)
    system_prompt: str | None = None
    params: dict | None = None
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class ChatSessionStore:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "chat_sessions.json")

    def _load(self) -> list[ChatSession]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as error:
            report_unreadable_store(self.path, error)
            return []
        if not isinstance(raw, list):
            return []
        sessions = []
        for item in raw:
            if not isinstance(item, dict) or "id" not in item:
                continue
            sessions.append(ChatSession(
                id=item["id"], title=item.get("title", "New chat"),
                messages=item.get("messages") or [], endpoint_id=item.get("endpoint_id", ""),
                model=item.get("model", ""), pinned=item.get("pinned", False),
                parent_session_id=item.get("parent_session_id", ""),
                branch_point=item.get("branch_point", -1),
                folder_id=item.get("folder_id", ""), archived=item.get("archived", False),
                tags=item.get("tags") or [],
                system_prompt=item.get("system_prompt"), params=item.get("params"),
                created=item.get("created", ""), updated=item.get("updated", ""),
            ))
        return sessions

    def branches_of(self, session_id: str) -> list[ChatSession]:
        return sorted((s for s in self._load() if s.parent_session_id == session_id),
                      key=lambda s: s.updated, reverse=True)

    def _save(self, sessions: list[ChatSession]) -> None:
        atomic_write_json(self.path, [asdict(s) for s in sessions])

    def list(self, query: str = "", archived: bool = False) -> list[ChatSession]:
        sessions = [s for s in self._load() if s.archived == archived]
        query = query.strip().lower()
        if query.startswith(TAG_QUERY_PREFIX):
            tag = normalize_tag(query[len(TAG_QUERY_PREFIX):])
            sessions = [s for s in sessions if tag in s.tags]
        elif query:
            sessions = [
                s for s in sessions
                if query in s.title.lower() or any(query in (m.get("content") or "").lower() for m in s.messages)
            ]
        sessions = sorted(sessions, key=lambda s: s.updated, reverse=True)
        return sorted(sessions, key=lambda s: s.pinned, reverse=True)

    def all(self) -> list[ChatSession]:
        return self._load()

    def tag_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for s in self._load():
            for tag in s.tags:
                counts[tag] = counts.get(tag, 0) + 1
        return dict(sorted(counts.items()))

    def get(self, session_id: str) -> ChatSession | None:
        for s in self._load():
            if s.id == session_id:
                return s
        return None

    def add(self, title: str = "New chat", messages: list[dict] | None = None,
            parent_session_id: str = "", branch_point: int = -1, **extra) -> ChatSession:
        return self.add_many([dict(title=title, messages=messages, parent_session_id=parent_session_id,
                                   branch_point=branch_point, **extra)])[0]

    @holds_file_lock()
    def add_many(self, items: list[dict]) -> list[ChatSession]:
        sessions = self._load()
        created = []
        for item in items:
            session = ChatSession(id=uuid.uuid4().hex[:12])
            for key, value in item.items():
                if key != "id" and hasattr(session, key) and value is not None:
                    setattr(session, key, value)
            session.title = session.title or "New chat"
            session.tags = sorted({normalize_tag(t) for t in session.tags if normalize_tag(t)})
            created.append(session)
        sessions.extend(created)
        self._save(sessions)
        return created

    def clone(self, session_id: str) -> ChatSession | None:
        source = self.get(session_id)
        if not source:
            return None
        return self.add(title=f"Clone of {source.title}", messages=copy.deepcopy(source.messages),
                        endpoint_id=source.endpoint_id, model=source.model, folder_id=source.folder_id,
                        tags=list(source.tags), system_prompt=source.system_prompt,
                        params=copy.deepcopy(source.params))

    @holds_file_lock()
    def update(self, session_id: str, **fields) -> bool:
        sessions = self._load()
        found = False
        for s in sessions:
            if s.id == session_id:
                for k, v in fields.items():
                    if v is not None and hasattr(s, k):
                        setattr(s, k, v)
                if fields.get("tags") is not None:
                    s.tags = sorted({normalize_tag(t) for t in s.tags if normalize_tag(t)})
                s.updated = datetime.now(timezone.utc).isoformat()
                found = True
        if found:
            self._save(sessions)
        return found

    @holds_file_lock()
    def delete(self, session_id: str) -> bool:
        sessions = self._load()
        remaining = [s for s in sessions if s.id != session_id]
        if len(remaining) == len(sessions):
            return False
        self._save(remaining)
        return True

    @holds_file_lock()
    def set_archived_all(self, archived: bool) -> int:
        sessions = self._load()
        changed = [s for s in sessions if s.archived != archived]
        for s in changed:
            s.archived = archived
        if changed:
            self._save(sessions)
        return len(changed)

    @holds_file_lock()
    def delete_all(self) -> int:
        count = len(self._load())
        self._save([])
        return count

    @holds_file_lock()
    def clear_folder(self, folder_id: str) -> None:
        sessions = self._load()
        moved = [s for s in sessions if s.folder_id == folder_id]
        for s in moved:
            s.folder_id = ""
        if moved:
            self._save(sessions)
