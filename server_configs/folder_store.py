from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from atomic_io import atomic_write_json, holds_file_lock, report_unreadable_store

MAX_FOLDER_NAME_LENGTH = 60

@dataclass
class Folder:
    id: str
    name: str
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class FolderStore:

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "chat_folders.json")

    def _load(self) -> list[Folder]:
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
        return [Folder(id=item["id"], name=item["name"], created=item.get("created", ""))
                for item in raw if isinstance(item, dict) and "id" in item and "name" in item]

    def _save(self, folders: list[Folder]) -> None:
        atomic_write_json(self.path, [asdict(f) for f in folders])

    @staticmethod
    def clean_name(name: str) -> str:
        cleaned = " ".join(name.split())[:MAX_FOLDER_NAME_LENGTH]
        if not cleaned:
            raise ValueError("folder name is empty")
        return cleaned

    def list(self) -> list[Folder]:
        return sorted(self._load(), key=lambda f: f.name.lower())

    def get(self, folder_id: str) -> Folder | None:
        return next((f for f in self._load() if f.id == folder_id), None)

    @holds_file_lock()
    def add(self, name: str) -> Folder:
        folders = self._load()
        folder = Folder(id=uuid.uuid4().hex[:12], name=self.clean_name(name))
        folders.append(folder)
        self._save(folders)
        return folder

    @holds_file_lock()
    def rename(self, folder_id: str, name: str) -> bool:
        folders = self._load()
        folder = next((f for f in folders if f.id == folder_id), None)
        if not folder:
            return False
        folder.name = self.clean_name(name)
        self._save(folders)
        return True

    @holds_file_lock()
    def delete(self, folder_id: str) -> bool:
        folders = self._load()
        remaining = [f for f in folders if f.id != folder_id]
        if len(remaining) == len(folders):
            return False
        self._save(remaining)
        return True
