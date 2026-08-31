"""Notes: Google-Keep-style notes and checklists with due dates, pins,
labels, colors, and repeat — odysseus-dev's actual to-do-list equivalent
(its "Tasks" subsystem, ported earlier, is scheduled agent automation, a
different feature despite the name).

Ported from odysseus-dev's Note model (core/database.py), scoped to JSON
storage (no DB) and the fields a to-do list actually needs — dropped:
owner/session_id (no multi-user/sessions here), image_url (no image
uploads), and ai_classification/ai_content_hash/agent_session_id (an AI
auto-triage + note-spawns-a-chat-session feature, not built here).
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from atomic_io import atomic_write_json

VALID_REPEATS = {"none", "daily", "weekly", "monthly", "yearly"}
VALID_NOTE_TYPES = {"note", "checklist"}


@dataclass
class NoteItem:
    text: str
    done: bool = False


@dataclass
class Note:
    id: str
    title: str = ""
    content: str = ""
    items: list[NoteItem] = field(default_factory=list)
    note_type: str = "note"  # note | checklist
    color: str = ""
    label: str = ""
    pinned: bool = False
    archived: bool = False
    due_date: str = ""  # ISO date/datetime string, or ""
    repeat: str = "none"  # none | daily | weekly | monthly | yearly
    source: str = "user"  # user | agent
    sort_order: int = 0
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class NoteStore:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "notes.json")

    def _load(self) -> list[Note]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        notes = []
        for item in raw:
            if not isinstance(item, dict) or "id" not in item:
                continue
            items = [NoteItem(text=i.get("text", ""), done=i.get("done", False))
                     for i in item.get("items", []) if isinstance(i, dict)]
            notes.append(Note(
                id=item["id"], title=item.get("title", ""), content=item.get("content", ""),
                items=items, note_type=item.get("note_type", "note"),
                color=item.get("color", ""), label=item.get("label", ""),
                pinned=item.get("pinned", False), archived=item.get("archived", False),
                due_date=item.get("due_date", ""), repeat=item.get("repeat", "none"),
                source=item.get("source", "user"), sort_order=item.get("sort_order", 0),
                created=item.get("created", ""),
            ))
        return notes

    def _save(self, notes: list[Note]) -> None:
        atomic_write_json(self.path, [asdict(n) for n in notes])

    def list(self, archived: bool | None = None, label: str | None = None) -> list[Note]:
        notes = self._load()
        if archived is not None:
            notes = [n for n in notes if n.archived == archived]
        if label:
            notes = [n for n in notes if n.label == label]
        # Pinned first, matching odysseus's Note.pinned.desc() list ordering.
        return sorted(notes, key=lambda n: (not n.pinned, n.sort_order, n.created))

    def get(self, note_id: str) -> Note | None:
        for n in self._load():
            if n.id == note_id:
                return n
        return None

    def add(self, title: str = "", content: str = "", items: list[dict] | None = None,
             note_type: str = "note", color: str = "", label: str = "", due_date: str = "",
             repeat: str = "none", source: str = "user") -> Note:
        if note_type not in VALID_NOTE_TYPES:
            note_type = "note"
        if repeat not in VALID_REPEATS:
            repeat = "none"
        notes = self._load()
        note = Note(
            id=uuid.uuid4().hex[:12], title=title, content=content,
            items=[NoteItem(text=i.get("text", ""), done=i.get("done", False)) for i in (items or [])],
            note_type=note_type, color=color, label=label, due_date=due_date, repeat=repeat,
            source=source, sort_order=len(notes),
        )
        notes.append(note)
        self._save(notes)
        return note

    def update(self, note_id: str, **fields) -> bool:
        notes = self._load()
        found = False
        for n in notes:
            if n.id == note_id:
                if "items" in fields and fields["items"] is not None:
                    fields["items"] = [NoteItem(text=i.get("text", ""), done=i.get("done", False))
                                        for i in fields["items"]]
                for k, v in fields.items():
                    if v is not None and hasattr(n, k):
                        setattr(n, k, v)
                found = True
        if found:
            self._save(notes)
        return found

    def delete(self, note_id: str) -> bool:
        notes = self._load()
        remaining = [n for n in notes if n.id != note_id]
        if len(remaining) == len(notes):
            return False
        self._save(remaining)
        return True

    def set_pinned(self, note_id: str, pinned: bool) -> bool:
        return self.update(note_id, pinned=pinned)

    def set_archived(self, note_id: str, archived: bool) -> bool:
        return self.update(note_id, archived=archived)

    def toggle_item(self, note_id: str, index: int) -> bool:
        notes = self._load()
        for n in notes:
            if n.id == note_id:
                if index < 0 or index >= len(n.items):
                    return False
                n.items[index].done = not n.items[index].done
                self._save(notes)
                return True
        return False


# ── note templates ───────────────────────────────────────────────────────
# A saved note/checklist shape (title, content or checklist items, type,
# label, color, repeat) you can instantiate with one click instead of
# rebuilding the same recurring structure — a weekly meeting-notes format,
# a standard shopping checklist, etc. Same domain as NoteStore above, kept
# in the same file rather than its own near-empty one.

@dataclass
class NoteTemplate:
    id: str
    name: str
    title: str = ""
    content: str = ""
    items: list[dict] = field(default_factory=list)  # [{text, done}], done always false when instantiated
    note_type: str = "note"
    label: str = ""
    color: str = ""
    repeat: str = "none"


class NoteTemplateStore:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "note_templates.json")

    def _load(self) -> list[NoteTemplate]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        templates = []
        for item in raw:
            if not isinstance(item, dict) or "id" not in item or "name" not in item:
                continue
            templates.append(NoteTemplate(
                id=item["id"], name=item["name"], title=item.get("title", ""),
                content=item.get("content", ""), items=item.get("items", []),
                note_type=item.get("note_type", "note"), label=item.get("label", ""),
                color=item.get("color", ""), repeat=item.get("repeat", "none"),
            ))
        return templates

    def _save(self, templates: list[NoteTemplate]) -> None:
        atomic_write_json(self.path, [asdict(t) for t in templates])

    def list(self) -> list[NoteTemplate]:
        return self._load()

    def get(self, template_id: str) -> NoteTemplate | None:
        for t in self._load():
            if t.id == template_id:
                return t
        return None

    def add(self, **fields) -> NoteTemplate:
        templates = self._load()
        template = NoteTemplate(id=uuid.uuid4().hex[:12], **fields)
        templates.append(template)
        self._save(templates)
        return template

    def delete(self, template_id: str) -> bool:
        templates = self._load()
        remaining = [t for t in templates if t.id != template_id]
        if len(remaining) == len(templates):
            return False
        self._save(remaining)
        return True
