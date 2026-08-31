"""Local ComfyUI configuration: one record, not a list — base URL, the local
directory its models/checkpoints folder lives in (needed for the checkpoint
downloader, since ComfyUI's own HTTP API manages workflows, not model
files), and which checkpoint to use by default. Same JSON-file pattern as
every other store here, just a single object instead of a list.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from atomic_io import atomic_write_json

DEFAULT_BASE_URL = "http://127.0.0.1:8188"


@dataclass
class ComfyUIConfig:
    base_url: str = DEFAULT_BASE_URL
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
            base_url=raw.get("base_url", DEFAULT_BASE_URL),
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
