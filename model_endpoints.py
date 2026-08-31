"""Model endpoint registry: a small JSON file, not a database.

odysseus-dev backs its ModelEndpoint table with SQLAlchemy because it also
tracks per-user ownership, multi-model overrides, etc. starfire is a single
local process with no multi-user surface, so a JSON file matches
api_key_manager.py's own file-based philosophy without dragging in a DB
dependency.

API keys themselves are NOT stored here — they live in the encrypted store
(api_key_manager.py), keyed by endpoint id. This file only ever holds
non-secret connection metadata.
"""

import json
import os
import uuid
from dataclasses import asdict, dataclass

from atomic_io import atomic_write_json
from providers import _detect_provider


@dataclass
class ModelEndpoint:
    id: str
    base_url: str
    kind: str  # "ollama" | "api-key"
    provider: str  # detected via providers._detect_provider, cached at add-time
    label: str = ""
    model_type: str = "chat"


class ModelEndpointStore:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "endpoints.json")

    def _load(self) -> list[ModelEndpoint]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        endpoints = []
        for item in raw:
            if not isinstance(item, dict) or "id" not in item or "base_url" not in item:
                continue
            endpoints.append(ModelEndpoint(
                id=item["id"],
                base_url=item["base_url"],
                kind=item.get("kind", "api-key"),
                provider=item.get("provider") or _detect_provider(item["base_url"]),
                label=item.get("label", ""),
                model_type=item.get("model_type", "chat"),
            ))
        return endpoints

    def _save(self, endpoints: list[ModelEndpoint]) -> None:
        atomic_write_json(self.path, [asdict(e) for e in endpoints])

    def list(self) -> list[ModelEndpoint]:
        return self._load()

    def get(self, endpoint_id: str) -> ModelEndpoint | None:
        for e in self._load():
            if e.id == endpoint_id:
                return e
        return None

    def find_by_url(self, base_url: str) -> ModelEndpoint | None:
        for e in self._load():
            if e.base_url.rstrip("/") == base_url.rstrip("/"):
                return e
        return None

    def add(self, base_url: str, kind: str, label: str = "", model_type: str = "chat") -> ModelEndpoint:
        endpoints = self._load()
        endpoint = ModelEndpoint(
            id=uuid.uuid4().hex[:12],
            base_url=base_url.rstrip("/"),
            kind=kind,
            provider=_detect_provider(base_url),
            label=label,
            model_type=model_type,
        )
        endpoints.append(endpoint)
        self._save(endpoints)
        return endpoint

    def delete(self, endpoint_id: str) -> bool:
        endpoints = self._load()
        remaining = [e for e in endpoints if e.id != endpoint_id]
        if len(remaining) == len(endpoints):
            return False
        self._save(remaining)
        return True
