from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from atomic_io import atomic_write_json, holds_file_lock, report_unreadable_store
from auto_model import AUTO

CHOICE_FILE = "model_choice.json"


@dataclass(frozen=True)
class ModelChoice:
    endpoint_id: str = AUTO
    model: str = AUTO

    @property
    def is_auto(self) -> bool:
        return self.model == AUTO


class ModelChoiceStore:

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, CHOICE_FILE)

    def get(self) -> ModelChoice:
        if not os.path.exists(self.path):
            return ModelChoice()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as error:
            report_unreadable_store(self.path, error)
            return ModelChoice()
        if not isinstance(raw, dict) or not isinstance(raw.get("model"), str) or not raw["model"]:
            return ModelChoice()
        if raw["model"] == AUTO:
            return ModelChoice()
        endpoint_id = raw.get("endpoint_id")
        return ModelChoice(endpoint_id=endpoint_id if isinstance(endpoint_id, str) else "", model=raw["model"])

    @holds_file_lock()
    def set(self, choice: ModelChoice) -> ModelChoice:
        atomic_write_json(self.path, asdict(choice))
        return choice
