"""Metadata + on-disk storage for generated images/audio/documents. One
store for all three kinds rather than three near-identical ones — they only
differ by content_type and file extension, everything else (save bytes to
data/generated/<id>.<ext>, keep a JSON index of what's there) is the same.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from atomic_io import atomic_write_json


@dataclass
class GeneratedFile:
    id: str
    kind: str  # image | audio | document
    filename: str
    content_type: str
    source: str = ""  # the prompt/content that produced it, for display
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class GeneratedFileStore:
    def __init__(self, data_dir: str):
        self.index_path = os.path.join(data_dir, "generated_files.json")
        self.files_dir = os.path.join(data_dir, "generated")
        os.makedirs(self.files_dir, exist_ok=True)

    def _load(self) -> list[GeneratedFile]:
        if not os.path.exists(self.index_path):
            return []
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        entries = []
        for item in raw:
            if not isinstance(item, dict) or "id" not in item:
                continue
            entries.append(GeneratedFile(
                id=item["id"], kind=item.get("kind", ""), filename=item.get("filename", ""),
                content_type=item.get("content_type", "application/octet-stream"),
                source=item.get("source", ""), created=item.get("created", ""),
            ))
        return entries

    def _save(self, entries: list[GeneratedFile]) -> None:
        atomic_write_json(self.index_path, [asdict(e) for e in entries])

    def list(self) -> list[GeneratedFile]:
        return sorted(self._load(), key=lambda e: e.created, reverse=True)

    def get(self, file_id: str) -> GeneratedFile | None:
        for e in self._load():
            if e.id == file_id:
                return e
        return None

    def path_for(self, entry: GeneratedFile) -> str:
        return os.path.join(self.files_dir, entry.id + os.path.splitext(entry.filename)[1])

    def add(self, kind: str, filename: str, content_type: str, data: bytes, source: str = "") -> GeneratedFile:
        entries = self._load()
        entry = GeneratedFile(id=uuid.uuid4().hex[:12], kind=kind, filename=filename,
                               content_type=content_type, source=source)
        with open(self.path_for(entry), "wb") as f:
            f.write(data)
        entries.append(entry)
        self._save(entries)
        return entry

    def delete(self, file_id: str) -> bool:
        entries = self._load()
        target = next((e for e in entries if e.id == file_id), None)
        if not target:
            return False
        try:
            os.remove(self.path_for(target))
        except OSError:
            pass
        self._save([e for e in entries if e.id != file_id])
        return True
