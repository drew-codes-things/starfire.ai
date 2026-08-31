"""Presets: a saved (system prompt + model + enabled tools) bundle you can
apply with one click instead of reconfiguring the header/toggles each time.
Same JSON-file store shape as everything else here.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field

from atomic_io import atomic_write_json


@dataclass
class Preset:
    id: str
    name: str
    system_prompt: str = ""
    endpoint_id: str = ""
    model: str = ""
    enabled_mcp_servers: list[str] = field(default_factory=list)
    enabled_builtin_tools: list[str] = field(default_factory=list)


class PresetStore:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "presets.json")

    def _load(self) -> list[Preset]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        presets = []
        for item in raw:
            if not isinstance(item, dict) or "id" not in item or "name" not in item:
                continue
            presets.append(Preset(
                id=item["id"], name=item["name"], system_prompt=item.get("system_prompt", ""),
                endpoint_id=item.get("endpoint_id", ""), model=item.get("model", ""),
                enabled_mcp_servers=item.get("enabled_mcp_servers", []),
                enabled_builtin_tools=item.get("enabled_builtin_tools", []),
            ))
        return presets

    def _save(self, presets: list[Preset]) -> None:
        atomic_write_json(self.path, [asdict(p) for p in presets])

    def list(self) -> list[Preset]:
        return self._load()

    def get(self, preset_id: str) -> Preset | None:
        for p in self._load():
            if p.id == preset_id:
                return p
        return None

    def add(self, **fields) -> Preset:
        presets = self._load()
        preset = Preset(id=uuid.uuid4().hex[:12], **fields)
        presets.append(preset)
        self._save(presets)
        return preset

    def delete(self, preset_id: str) -> bool:
        presets = self._load()
        remaining = [p for p in presets if p.id != preset_id]
        if len(remaining) == len(presets):
            return False
        self._save(remaining)
        return True
