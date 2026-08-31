"""Note templates: a saved note/checklist shape (title, content or checklist
items, type, label, color, repeat) you can instantiate with one click
instead of rebuilding the same recurring structure — a weekly meeting-notes
format, a standard shopping checklist, etc. Same JSON-file store shape as
everything else here.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field

from atomic_io import atomic_write_json


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
