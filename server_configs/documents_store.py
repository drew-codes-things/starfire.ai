from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import asdict, dataclass

import embeddings as embeddings_module
import lexical_search
from atomic_io import atomic_write_json, holds_file_lock, set_aside_unreadable
from config import config
from model_endpoints import detect_ollama

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200

logger = logging.getLogger(__name__)

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

def _pieces_no_longer_than(chunk_size: int, sentences: list[str]) -> list[str]:
    return [sentence[start:start + chunk_size] for sentence in sentences
            for start in range(0, len(sentence), chunk_size)]

def split_into_chunks(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    sentences = _pieces_no_longer_than(chunk_size, [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()])
    if not sentences:
        return []

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = (current + " " + sentence).strip() if current else sentence
        if len(candidate) > chunk_size and current:
            chunks.append(current)
            tail = current[-overlap:] if overlap < len(current) else current
            current = (tail + " " + sentence).strip()
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks

def extract_text(filename: str, raw: bytes) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        from io import BytesIO
        reader = PdfReader(BytesIO(raw))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    return raw.decode("utf-8", errors="replace")

@dataclass
class DocumentChunk:
    id: str
    document_id: str
    text: str
    chunk_index: int
    embedding: list[float] | None = None

@dataclass
class Document:
    id: str
    filename: str
    added: str
    chunk_count: int

class DocumentStore:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "documents.json")

    def _start_again_without(self, reason: str) -> dict:
        aside = set_aside_unreadable(self.path)
        logger.error("documents file could not be read because %s; moved it to %s and starting empty", reason, aside)
        return {"documents": [], "chunks": []}

    def _load_raw(self) -> dict:
        if not os.path.exists(self.path):
            return {"documents": [], "chunks": []}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except json.JSONDecodeError as e:
            return self._start_again_without(f"it is not valid JSON ({e})")
        if not isinstance(raw, dict):
            return self._start_again_without(f"it holds a {type(raw).__name__}, not an object")
        return {"documents": raw.get("documents", []), "chunks": raw.get("chunks", [])}

    def _save_raw(self, data: dict) -> None:
        atomic_write_json(self.path, data)

    def list(self) -> list[Document]:
        raw = self._load_raw()
        return [Document(**d) for d in raw["documents"]]

    @holds_file_lock()
    def add(self, filename: str, raw_bytes: bytes) -> Document:
        text = extract_text(filename, raw_bytes)
        pieces = split_into_chunks(text)
        if not pieces:
            raise ValueError("no extractable text in this file")

        from datetime import datetime, timezone
        data = self._load_raw()
        doc = Document(id=uuid.uuid4().hex[:12], filename=filename,
                        added=datetime.now(timezone.utc).isoformat(), chunk_count=len(pieces))
        data["documents"].append(asdict(doc))
        for i, piece in enumerate(pieces):
            chunk = DocumentChunk(id=uuid.uuid4().hex[:12], document_id=doc.id, text=piece, chunk_index=i)
            data["chunks"].append(asdict(chunk))
        self._save_raw(data)
        return doc

    @holds_file_lock()
    def delete(self, document_id: str) -> bool:
        data = self._load_raw()
        remaining_docs = [d for d in data["documents"] if d["id"] != document_id]
        if len(remaining_docs) == len(data["documents"]):
            return False
        data["documents"] = remaining_docs
        data["chunks"] = [c for c in data["chunks"] if c["document_id"] != document_id]
        self._save_raw(data)
        return True

    def full_text(self, document_id: str) -> tuple[str, str] | None:
        data = self._load_raw()
        doc = next((d for d in data["documents"] if d["id"] == document_id), None)
        if not doc:
            return None
        chunks = sorted((c for c in data["chunks"] if c["document_id"] == document_id),
                        key=lambda c: c["chunk_index"])
        parts: list[str] = []
        previous = ""
        for chunk in chunks:
            text = chunk["text"]
            overlap = previous[-CHUNK_OVERLAP:].strip() if previous else ""
            if overlap and text.startswith(overlap):
                text = text[len(overlap):].lstrip()
            parts.append(text)
            previous = chunk["text"]
        return doc["filename"], " ".join(p for p in parts if p)

    async def search(self, query: str, k: int = 5) -> list[dict]:
        data = self._load_raw()
        chunks = data["chunks"]
        lexical_scores = {
            c["id"]: lexical_search.score(lexical_search.tokenize(query), lexical_search.tokenize(c["text"]))
            for c in chunks
        }
        ranked_ids = await self._rank_chunks(query, chunks, lexical_scores)

        by_id = {c["id"]: c for c in chunks}
        doc_names = {d["id"]: d["filename"] for d in data["documents"]}
        return [
            {"text": by_id[i]["text"], "document_id": by_id[i]["document_id"],
             "filename": doc_names.get(by_id[i]["document_id"], "unknown")}
            for i in ranked_ids[:k] if i in by_id
        ]

    async def _rank_chunks(self, query: str, chunks: list[dict], lexical_scores: dict[str, float]) -> list[str]:
        lexical_ranked = [i for i, s in sorted(lexical_scores.items(), key=lambda p: p[1], reverse=True)
                            if s >= 0.03]

        ollama_url = await detect_ollama(config.ollama_base_url)
        if not ollama_url or not chunks:
            return lexical_ranked

        query_vec = await embeddings_module.embed(query, ollama_url)
        if not query_vec:
            return lexical_ranked
        new_vectors = await self._ensure_embeddings(chunks, ollama_url)
        if new_vectors:
            self._store_embeddings(new_vectors)

        semantic_scores = {
            c["id"]: embeddings_module.cosine_similarity(query_vec, c["embedding"])
            for c in chunks if c.get("embedding")
        }
        if not semantic_scores:
            return lexical_ranked
        blended = embeddings_module.blend_scores(lexical_scores, semantic_scores)
        return [i for i, _ in sorted(blended.items(), key=lambda p: p[1], reverse=True)]

    @staticmethod
    async def _ensure_embeddings(chunks: list[dict], ollama_url: str) -> dict[str, list[float]]:
        new_vectors = {}
        for chunk in chunks:
            if chunk.get("embedding"):
                continue
            vector = await embeddings_module.embed(chunk["text"], ollama_url)
            if vector:
                chunk["embedding"] = vector
                new_vectors[chunk["id"]] = vector
        return new_vectors

    @holds_file_lock()
    def _store_embeddings(self, vectors: dict[str, list[float]]) -> None:
        data = self._load_raw()
        changed = False
        for chunk in data["chunks"]:
            if chunk["id"] in vectors:
                chunk["embedding"] = vectors[chunk["id"]]
                changed = True
        if changed:
            self._save_raw(data)
