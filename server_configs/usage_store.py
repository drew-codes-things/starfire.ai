from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from atomic_io import atomic_write_json
from context_budget import estimate_tokens

_PRICING_PER_MILLION = {
    "claude-opus": (15.0, 75.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku": (0.8, 4.0),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4": (30.0, 60.0),
    "gpt-3.5": (0.5, 1.5),
}

def _estimate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> float | None:
    if provider == "ollama":
        return 0.0
    model_lower = (model or "").lower()
    for prefix, (in_price, out_price) in _PRICING_PER_MILLION.items():
        if prefix in model_lower:
            return (input_tokens * in_price + output_tokens * out_price) / 1_000_000
    return None

@dataclass
class UsageEntry:
    id: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost: float | None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class UsageStore:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "usage.json")

    def _load(self) -> list[UsageEntry]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        entries = []
        for item in raw:
            if not isinstance(item, dict) or "id" not in item:
                continue
            entries.append(UsageEntry(
                id=item["id"], provider=item.get("provider", ""), model=item.get("model", ""),
                input_tokens=item.get("input_tokens", 0), output_tokens=item.get("output_tokens", 0),
                cost=item.get("cost"), timestamp=item.get("timestamp", ""),
            ))
        return entries

    def _save(self, entries: list[UsageEntry]) -> None:
        atomic_write_json(self.path, [asdict(e) for e in entries])

    def record(self, provider: str, model: str, input_text: str, output_text: str) -> UsageEntry:
        input_tokens = estimate_tokens(input_text)
        output_tokens = estimate_tokens(output_text)
        cost = _estimate_cost(provider, model, input_tokens, output_tokens)
        entry = UsageEntry(id=uuid.uuid4().hex[:12], provider=provider, model=model,
                            input_tokens=input_tokens, output_tokens=output_tokens, cost=cost)
        entries = self._load()
        entries.append(entry)
        entries = entries[-5000:]
        self._save(entries)
        return entry

    def summary(self) -> dict:
        entries = self._load()
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()

        def _totals(rows: list[UsageEntry]) -> dict:
            return {
                "turns": len(rows),
                "input_tokens": sum(r.input_tokens for r in rows),
                "output_tokens": sum(r.output_tokens for r in rows),
                "cost": round(sum(r.cost for r in rows if r.cost), 4) if any(r.cost for r in rows) else 0.0,
            }

        today_rows = [e for e in entries if e.timestamp.startswith(today)]

        by_model: dict[str, list[UsageEntry]] = {}
        for e in entries:
            by_model.setdefault(f"{e.provider}/{e.model}" if e.model else e.provider, []).append(e)

        return {
            "today": _totals(today_rows),
            "all_time": _totals(entries),
            "by_model": {k: _totals(v) for k, v in sorted(by_model.items())},
        }
