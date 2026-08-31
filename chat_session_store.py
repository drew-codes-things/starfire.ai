from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from atomic_io import atomic_write_json

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
        except (json.JSONDecodeError, OSError):
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
                created=item.get("created", ""), updated=item.get("updated", ""),
            ))
        return sessions

    def branches_of(self, session_id: str) -> list[ChatSession]:
        return sorted((s for s in self._load() if s.parent_session_id == session_id),
                      key=lambda s: s.updated, reverse=True)

    def _save(self, sessions: list[ChatSession]) -> None:
        atomic_write_json(self.path, [asdict(s) for s in sessions])

    def list(self, query: str = "") -> list[ChatSession]:
        sessions = self._load()
        if query:
            q = query.lower()
            sessions = [
                s for s in sessions
                if q in s.title.lower() or any(q in (m.get("content") or "").lower() for m in s.messages)
            ]
        sessions = sorted(sessions, key=lambda s: s.updated, reverse=True)
        return sorted(sessions, key=lambda s: s.pinned, reverse=True)

    def get(self, session_id: str) -> ChatSession | None:
        for s in self._load():
            if s.id == session_id:
                return s
        return None

    def add(self, title: str = "New chat", messages: list[dict] | None = None,
            parent_session_id: str = "", branch_point: int = -1) -> ChatSession:
        sessions = self._load()
        session = ChatSession(id=uuid.uuid4().hex[:12], title=title or "New chat",
                               messages=messages or [], parent_session_id=parent_session_id,
                               branch_point=branch_point)
        sessions.append(session)
        self._save(sessions)
        return session

    def update(self, session_id: str, **fields) -> bool:
        sessions = self._load()
        found = False
        for s in sessions:
            if s.id == session_id:
                for k, v in fields.items():
                    if v is not None and hasattr(s, k):
                        setattr(s, k, v)
                s.updated = datetime.now(timezone.utc).isoformat()
                found = True
        if found:
            self._save(sessions)
        return found

    def delete(self, session_id: str) -> bool:
        sessions = self._load()
        remaining = [s for s in sessions if s.id != session_id]
        if len(remaining) == len(sessions):
            return False
        self._save(remaining)
        return True
