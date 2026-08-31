"""Saved custom ComfyUI workflows — the video generation path (see
comfyui_client.py's module docstring for why video has no fixed built-in
workflow the way images do). You export a working workflow as JSON from
ComfyUI's own UI, tell starfire which node/input holds the prompt text, and
save it here under a name; the generate_video tool then queues it by name
with your prompt substituted in.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field

from atomic_io import atomic_write_json


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
