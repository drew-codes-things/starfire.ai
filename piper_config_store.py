"""Local Piper TTS configuration: one record — which voice model file (.onnx)
to use, and the directory new voices get downloaded into. Same
single-record pattern as comfyui_config_store.py.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from atomic_io import atomic_write_json


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
