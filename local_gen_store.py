from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from atomic_io import atomic_write_json

DEFAULT_COMFYUI_BASE_URL = "http://127.0.0.1:8188"

@dataclass
class ComfyUIConfig:
    base_url: str = DEFAULT_COMFYUI_BASE_URL
    checkpoints_dir: str = ""
    default_checkpoint: str = ""
    default_negative_prompt: str = ""

class ComfyUIConfigStore:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "comfyui_config.json")

    def get(self) -> ComfyUIConfig:
        if not os.path.exists(self.path):
            return ComfyUIConfig()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return ComfyUIConfig()
        if not isinstance(raw, dict):
            return ComfyUIConfig()
        return ComfyUIConfig(
            base_url=raw.get("base_url", DEFAULT_COMFYUI_BASE_URL),
            checkpoints_dir=raw.get("checkpoints_dir", ""),
            default_checkpoint=raw.get("default_checkpoint", ""),
            default_negative_prompt=raw.get("default_negative_prompt", ""),
        )

    def update(self, **fields) -> ComfyUIConfig:
        current = self.get()
        for k, v in fields.items():
            if v is not None and hasattr(current, k):
                setattr(current, k, v)
        atomic_write_json(self.path, asdict(current))
        return current

@dataclass
class PiperConfig:
    voice_model_path: str = ""
    voices_dir: str = ""

class PiperConfigStore:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "piper_config.json")

    def get(self) -> PiperConfig:
        if not os.path.exists(self.path):
            return PiperConfig()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return PiperConfig()
        if not isinstance(raw, dict):
            return PiperConfig()
        return PiperConfig(voice_model_path=raw.get("voice_model_path", ""), voices_dir=raw.get("voices_dir", ""))

    def update(self, **fields) -> PiperConfig:
        current = self.get()
        for k, v in fields.items():
            if v is not None and hasattr(current, k):
                setattr(current, k, v)
        atomic_write_json(self.path, asdict(current))
        return current

@dataclass
class CustomWorkflow:
    id: str
    name: str
    workflow: dict = field(default_factory=dict)
    prompt_node_id: str = ""
    prompt_input_key: str = "text"

class CustomWorkflowStore:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "custom_workflows.json")

    def _load(self) -> list[CustomWorkflow]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        workflows = []
        for item in raw:
            if not isinstance(item, dict) or "id" not in item or "name" not in item:
                continue
            workflows.append(CustomWorkflow(
                id=item["id"], name=item["name"], workflow=item.get("workflow") or {},
                prompt_node_id=item.get("prompt_node_id", ""),
                prompt_input_key=item.get("prompt_input_key", "text"),
            ))
        return workflows

    def _save(self, workflows: list[CustomWorkflow]) -> None:
        atomic_write_json(self.path, [asdict(w) for w in workflows])

    def list(self) -> list[CustomWorkflow]:
        return self._load()

    def get(self, workflow_id: str) -> CustomWorkflow | None:
        for w in self._load():
            if w.id == workflow_id:
                return w
        return None

    def add(self, name: str, workflow: dict, prompt_node_id: str, prompt_input_key: str = "text") -> CustomWorkflow:
        workflows = self._load()
        entry = CustomWorkflow(id=uuid.uuid4().hex[:12], name=name, workflow=workflow,
                                prompt_node_id=prompt_node_id, prompt_input_key=prompt_input_key)
        workflows.append(entry)
        self._save(workflows)
        return entry

    def delete(self, workflow_id: str) -> bool:
        workflows = self._load()
        remaining = [w for w in workflows if w.id != workflow_id]
        if len(remaining) == len(workflows):
            return False
        self._save(remaining)
        return True

@dataclass
class GeneratedFile:
    id: str
    kind: str
    filename: str
    content_type: str
    source: str = ""
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
