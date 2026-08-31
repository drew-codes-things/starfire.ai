from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import embeddings as embeddings_module
import lexical_search
from atomic_io import atomic_write_json
from config import config
from model_discovery import detect_ollama

VALID_CATEGORIES = {"fact", "identity", "preference", "contact", "task"}

_CATEGORY_BOOST_KEYWORDS = {
    "identity": {"name", "who", "call", "i", "me", "my"},
    "contact": {"email", "phone", "address", "contact", "reach"},
    "preference": {"like", "prefer", "favorite", "favourite", "hate", "dislike"},
    "task": {"todo", "task", "remind", "deadline", "due"},
}

@dataclass
class MemoryEntry:
    id: str
    text: str
    category: str = "fact"
    source: str = "user"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    pinned: bool = False
    uses: int = 0
    embedding: list[float] = field(default_factory=list)

class MemoryStore:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "memory.json")

    def _load(self) -> list[MemoryEntry]:
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
            if not isinstance(item, dict) or "id" not in item or "text" not in item:
                continue
            entries.append(MemoryEntry(
                id=item["id"], text=item["text"],
                category=item.get("category", "fact"),
                source=item.get("source", "user"),
                timestamp=item.get("timestamp", ""),
                pinned=item.get("pinned", False),
                uses=item.get("uses", 0),
                embedding=item.get("embedding") or [],
            ))
        return entries

    def _save(self, entries: list[MemoryEntry]) -> None:
        atomic_write_json(self.path, [asdict(e) for e in entries])

    def list(self) -> list[MemoryEntry]:
        return self._load()

    def get(self, memory_id: str) -> MemoryEntry | None:
        for e in self._load():
            if e.id == memory_id:
                return e
        return None

    def find_duplicate(self, text: str) -> MemoryEntry | None:
        normalized = text.strip().lower()
        for e in self._load():
            if e.text.strip().lower() == normalized:
                return e
        return None

    def add(self, text: str, category: str = "fact", source: str = "user") -> MemoryEntry:
        text = text.strip()
        existing = self.find_duplicate(text)
        if existing:
            return existing
        if category not in VALID_CATEGORIES:
            category = "fact"
        entries = self._load()
        entry = MemoryEntry(id=uuid.uuid4().hex[:12], text=text, category=category, source=source)
        entries.append(entry)
        self._save(entries)
        return entry

    def update(self, memory_id: str, text: str | None = None, category: str | None = None) -> bool:
        entries = self._load()
        found = False
        for e in entries:
            if e.id == memory_id:
                if text is not None:
                    e.text = text.strip()
                if category is not None and category in VALID_CATEGORIES:
                    e.category = category
                found = True
        if found:
            self._save(entries)
        return found

    def delete(self, memory_id: str) -> bool:
        entries = self._load()
        remaining = [e for e in entries if e.id != memory_id]
        if len(remaining) == len(entries):
            return False
        self._save(remaining)
        return True

    def set_pinned(self, memory_id: str, pinned: bool) -> bool:
        entries = self._load()
        found = False
        for e in entries:
            if e.id == memory_id:
                e.pinned = pinned
                found = True
        if found:
            self._save(entries)
        return found

    async def relevant(self, query: str, max_items: int = 8) -> list[MemoryEntry]:
        entries = self._load()
        pinned = [e for e in entries if e.pinned]
        unpinned = [e for e in entries if not e.pinned]

        query_tokens = lexical_search.tokenize(query)
        boosted_ids = set()
        for category, keywords in _CATEGORY_BOOST_KEYWORDS.items():
            if query_tokens & keywords:
                boosted_ids |= {e.id for e in unpinned if e.category == category}

        remaining_slots = max(max_items - len(pinned), 0)
        lexical_scores = {
            e.id: lexical_search.score(query_tokens, lexical_search.tokenize(e.text))
            for e in unpinned
        }
        ordered_ids = await self._rank_unpinned(query, unpinned, lexical_scores, boosted_ids)

        by_id = {e.id: e for e in unpinned}
        picked = [by_id[i] for i in ordered_ids if i in by_id][:remaining_slots]

        used_ids = {e.id for e in pinned + picked}
        if used_ids:
            self._touch(used_ids)
        return pinned + picked

    async def _rank_unpinned(self, query: str, unpinned: list[MemoryEntry],
                              lexical_scores: dict[str, float], boosted_ids: set[str]) -> list[str]:
        lexical_ranked = [i for i, s in sorted(lexical_scores.items(), key=lambda p: p[1], reverse=True)
                            if s >= 0.05]

        ollama_url = await detect_ollama(config.ollama_base_url)
        if not ollama_url or not unpinned:
            return list(dict.fromkeys(lexical_ranked + [i for i in boosted_ids if i not in lexical_ranked]))

        await self._ensure_embeddings(unpinned, ollama_url)
        query_vec = await embeddings_module.embed(query, ollama_url)
        semantic_scores = {}
        if query_vec:
            semantic_scores = {
                e.id: embeddings_module.cosine_similarity(query_vec, e.embedding)
                for e in unpinned if e.embedding
            }
        blended = embeddings_module.blend_scores(lexical_scores, semantic_scores)
        ranked = [i for i, _ in sorted(blended.items(), key=lambda p: p[1], reverse=True)]
        return list(dict.fromkeys(ranked + [i for i in boosted_ids if i not in ranked]))

    async def _ensure_embeddings(self, entries: list[MemoryEntry], ollama_url: str) -> None:
        missing = [e for e in entries if not e.embedding]
        if not missing:
            return
        all_entries = self._load()
        by_id = {e.id: e for e in all_entries}
        changed = False
        for entry in missing:
            vector = await embeddings_module.embed(entry.text, ollama_url)
            if vector and entry.id in by_id:
                by_id[entry.id].embedding = vector
                entry.embedding = vector
                changed = True
        if changed:
            self._save(all_entries)

    def _touch(self, memory_ids: set[str]) -> None:
        entries = self._load()
        changed = False
        for e in entries:
            if e.id in memory_ids:
                e.uses += 1
                changed = True
        if changed:
            self._save(entries)
