import asyncio
import json
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import httpx
import pytest

import documents_store
import embeddings
import memory_store
from documents_store import DocumentStore
from memory_store import MemoryStore

OLLAMA = "http://ollama.example.test:11434"
TOPIC_VECTORS = {
    "pet": [1.0, 0.0, 0.0],
    "work": [0.0, 1.0, 0.0],
    "sea": [0.0, 0.0, 1.0],
}
TOPIC_WORDS = {
    "pet": ("dog", "rex", "pet", "animal"),
    "work": ("bakery", "job", "work", "employer"),
    "sea": ("boat", "sail", "sea", "harbour"),
}


def topic_vector(text):
    lowered = text.lower()
    for topic, words in TOPIC_WORDS.items():
        if any(word in lowered for word in words):
            return TOPIC_VECTORS[topic]
    return [0.3, 0.3, 0.3]


@pytest.fixture
def ollama(monkeypatch):
    state = {"reachable": True, "embedding_model": True, "embedded": []}

    async def detect(_override):
        return OLLAMA if state["reachable"] else None

    def handle(request):
        prompt = json.loads(request.content)["prompt"]
        if not state["embedding_model"]:
            return httpx.Response(404, json={"error": "model 'nomic-embed-text' not found, try pulling it first"})
        state["embedded"].append(prompt)
        return httpx.Response(200, json={"embedding": topic_vector(prompt)})

    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(embeddings.httpx, "AsyncClient", client)
    monkeypatch.setattr(memory_store, "detect_ollama", detect)
    monkeypatch.setattr(documents_store, "detect_ollama", detect)
    return state


def test_an_embedding_comes_back_as_its_vector(ollama):
    assert asyncio.run(embeddings.embed("my dog", OLLAMA)) == TOPIC_VECTORS["pet"]


def test_an_embedding_model_that_is_not_pulled_gives_no_vector(ollama):
    ollama["embedding_model"] = False
    assert asyncio.run(embeddings.embed("my dog", OLLAMA)) is None


@pytest.mark.parametrize("text, url", [("", OLLAMA), ("   ", OLLAMA), ("my dog", ""), ("my dog", None)])
def test_nothing_is_asked_for_empty_text_or_without_ollama(ollama, text, url):
    assert asyncio.run(embeddings.embed(text, url)) is None
    assert ollama["embedded"] == []


def test_an_ollama_that_cannot_be_reached_gives_no_vector_and_a_warning(monkeypatch, caplog):
    real_client = httpx.AsyncClient

    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(embeddings.httpx, "AsyncClient",
                        lambda *args, **kwargs: real_client(*args, transport=httpx.MockTransport(refuse), **kwargs))
    with caplog.at_level(logging.WARNING, logger="embeddings"):
        assert asyncio.run(embeddings.embed("my dog", OLLAMA)) is None
    assert "could not get an embedding" in caplog.text


@pytest.mark.parametrize("payload", [{}, {"embedding": []}, {"embedding": "not a list"}])
def test_an_answer_without_a_usable_vector_gives_no_vector(monkeypatch, payload):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(embeddings.httpx, "AsyncClient", lambda *args, **kwargs: real_client(
        *args, transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)), **kwargs))
    assert asyncio.run(embeddings.embed("my dog", OLLAMA)) is None


def memories(tmp_path):
    store = MemoryStore(str(tmp_path))
    for text in ("Rex is a golden retriever", "Works shifts at the bakery on Elm Street", "Prefers green tea"):
        store.add(text)
    return store


def texts(entries):
    return [entry.text for entry in entries]


def test_memory_finds_a_paraphrase_with_no_shared_words_when_embeddings_work(tmp_path, ollama):
    store = memories(tmp_path)
    found = asyncio.run(store.relevant("which animal lives with us", max_items=1))
    assert texts(found) == ["Rex is a golden retriever"]


def test_memory_embeds_each_entry_once_and_keeps_the_vectors(tmp_path, ollama):
    store = memories(tmp_path)
    asyncio.run(store.relevant("employer"))
    asyncio.run(store.relevant("harbour"))
    entry_texts = [text for text in ollama["embedded"] if text not in ("employer", "harbour")]
    assert sorted(entry_texts) == sorted(texts(store.list()))
    assert all(entry.embedding for entry in MemoryStore(str(tmp_path)).list())


def knowledge(tmp_path):
    store = DocumentStore(str(tmp_path))
    store.add("pets.txt", b"Rex is a golden retriever who sleeps by the fire.")
    store.add("shifts.txt", b"Shifts at the bakery on Elm Street start at five.")
    return store


def test_documents_find_a_paraphrase_with_no_shared_words_when_embeddings_work(tmp_path, ollama):
    store = knowledge(tmp_path)
    found = asyncio.run(store.search("which animal lives with us", k=1))
    assert [hit["filename"] for hit in found] == ["pets.txt"]


def test_documents_embed_each_chunk_once_and_keep_the_vectors(tmp_path, ollama):
    store = knowledge(tmp_path)
    asyncio.run(store.search("employer"))
    asyncio.run(store.search("harbour"))
    chunk_texts = [text for text in ollama["embedded"] if text not in ("employer", "harbour")]
    stored = DocumentStore(str(tmp_path))._load_raw()["chunks"]
    assert sorted(chunk_texts) == sorted(chunk["text"] for chunk in stored)
    assert all(chunk.get("embedding") for chunk in stored)
