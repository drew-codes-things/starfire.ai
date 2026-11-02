import asyncio
import base64
import os
import sys

import httpx
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import attachments
from attachments import AttachmentError, AttachmentStore, expand_attachments, files_url

BRAIN = "http://127.0.0.1:8081/v1"
PNG = b"\x89PNG fake image bytes"


async def chunks_of(*parts):
    for part in parts:
        yield part


def save(store, name, mime, *parts):
    return asyncio.run(store.save(name, mime, chunks_of(*parts)))


def expand(messages, store, base_url, label="Arty"):
    return asyncio.run(expand_attachments(messages, store, base_url, "secret-key", label))


@pytest.fixture
def store(tmp_path):
    return AttachmentStore(str(tmp_path))


@pytest.fixture
def fake_brain(monkeypatch):
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        if request.url.path != "/v1/files":
            return httpx.Response(404)
        return httpx.Response(200, json={"id": f"brain-{len(calls)}"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(attachments.httpx, "AsyncClient",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    return calls


def test_save_keeps_images_audio_and_video_and_cleans_the_name(store):
    image = save(store, "../../etc/holiday.png", "image/png", PNG[:5], PNG[5:])
    assert (image.kind, image.name, image.size) == ("image", "holiday.png", len(PNG))
    assert store.read_bytes(store.get(image.id)) == PNG
    assert save(store, "note.m4a", "audio/mp4", b"a").kind == "audio"
    assert save(store, "clip.mp4", "video/mp4", b"v").kind == "video"


@pytest.mark.parametrize("mime, parts", [("application/zip", [b"x"]), ("image/png", [])])
def test_save_rejects_other_types_and_empty_files(store, mime, parts):
    with pytest.raises(AttachmentError):
        save(store, "file", mime, *parts)
    assert os.listdir(store.root) == []


def test_save_enforces_the_size_limit_without_leaving_a_partial_file(store, monkeypatch):
    monkeypatch.setitem(attachments.MAX_BYTES, "image", 4)
    with pytest.raises(AttachmentError, match="limit"):
        save(store, "big.png", "image/png", b"123", b"456")
    assert os.listdir(store.root) == []


def test_get_rejects_ids_that_could_escape_the_folder(store):
    with pytest.raises(AttachmentError):
        store.get("../users")


def test_prune_removes_only_old_attachments(store):
    old = save(store, "old.png", "image/png", PNG)
    new = save(store, "new.png", "image/png", PNG)
    assert store.prune(now=old.created + attachments.KEEP_SECONDS + 1) == 2
    assert store.prune() == 0
    with pytest.raises(AttachmentError):
        store.get(new.id)


def test_files_url_sits_next_to_models():
    assert files_url(BRAIN) == "http://127.0.0.1:8081/v1/files"
    assert files_url("http://example.com") == "http://example.com/v1/files"


def test_messages_without_attachments_are_untouched_apart_from_the_key(store):
    messages = [{"role": "user", "content": "hi", "attachments": []}]
    assert expand(messages, store, "http://127.0.0.1:11434") == [{"role": "user", "content": "hi"}]


def test_images_go_to_ollama_as_base64(store):
    image = save(store, "cat.png", "image/png", PNG)
    [message] = expand([{"role": "user", "content": "what is it", "attachments": [image.id]}],
                       store, "http://127.0.0.1:11434")
    assert message == {"role": "user", "content": "what is it", "images": [base64.b64encode(PNG).decode()]}


def test_images_go_to_openai_style_endpoints_as_data_urls(store, fake_brain):
    image = save(store, "cat.png", "image/png", PNG)
    [message] = expand([{"role": "user", "content": "what is it", "attachments": [image.id]}], store, BRAIN)
    assert message["content"][0] == {"type": "text", "text": "what is it"}
    assert message["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert fake_brain == []


def test_images_go_to_anthropic_as_base64_blocks(store):
    image = save(store, "cat.png", "image/png", PNG)
    [message] = expand([{"role": "user", "content": "hi", "attachments": [image.id]}],
                       store, "https://api.anthropic.com")
    assert message["content"][1]["source"] == {"type": "base64", "media_type": "image/png",
                                               "data": base64.b64encode(PNG).decode()}


def test_audio_is_uploaded_to_arty_once_and_sent_as_a_file_part(store, fake_brain):
    clip = save(store, "voice note.m4a", "audio/mp4", b"audio bytes")
    messages = [{"role": "user", "content": "listen", "attachments": [clip.id]}]
    [first] = expand(messages, store, BRAIN)
    [second] = expand(messages, store, BRAIN)

    assert len(fake_brain) == 1
    request = fake_brain[0]
    assert request.headers["authorization"] == "Bearer secret-key"
    assert request.headers["content-type"] == "audio/mp4"
    assert request.headers["x-filename"] == "voice%20note.m4a"
    assert request.content == b"audio bytes"
    assert first["content"][1] == {"type": "file", "file": {"file_id": "brain-1", "filename": "voice note.m4a"}}
    assert second == first


def test_expired_remote_ids_are_uploaded_again(store, fake_brain, monkeypatch):
    clip = save(store, "clip.mp4", "video/mp4", b"video")
    messages = [{"role": "user", "content": "watch", "attachments": [clip.id]}]
    expand(messages, store, BRAIN)
    later = attachments.time.time() + attachments.REMOTE_ID_LIFETIME_SECONDS + 1
    monkeypatch.setattr(attachments.time, "time", lambda: later)
    expand(messages, store, BRAIN)
    assert len(fake_brain) == 2


def test_audio_to_a_model_that_cannot_take_it_explains_why(store):
    clip = save(store, "clip.mp4", "video/mp4", b"video")
    with pytest.raises(AttachmentError, match="send them to Arty"):
        expand([{"role": "user", "content": "watch", "attachments": [clip.id]}],
               store, "http://127.0.0.1:11434", label="Ollama")


def test_endpoints_without_a_files_api_get_a_clear_error(store, fake_brain):
    clip = save(store, "clip.mp4", "video/mp4", b"video")
    with pytest.raises(AttachmentError, match="can't take video"):
        expand([{"role": "user", "content": "watch", "attachments": [clip.id]}],
               store, "http://127.0.0.1:9999/other", label="Other")


def test_missing_attachments_say_to_attach_again(store):
    with pytest.raises(AttachmentError, match="attach the file again"):
        expand([{"role": "user", "content": "x", "attachments": ["0" * 32]}], store, BRAIN)
