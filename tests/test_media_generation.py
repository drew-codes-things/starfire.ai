import asyncio
import base64
import json
import logging
import os
import sys
from urllib.parse import parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import httpx
import pytest

import builtin_tools
import comfyui_client
import piper_tts
from api_key_manager import APIKeyManager
from builtin_tools import ToolContext
from documents_store import DocumentStore
from email_store import EmailAccountStore
from local_gen_store import ComfyUIConfigStore, CustomWorkflowStore, GeneratedFileStore, PiperConfigStore
from memory_store import MemoryStore
from model_endpoints import ModelEndpointStore
from note_store import NoteStore
from task_store import TaskRunStore, TaskStore

COMFY = "http://comfy.test:8188"
PNG = b"\x89PNG\r\n\x1a\nfake image bytes"
MP4 = b"\x00\x00\x00\x18ftypmp42fake video"
MP3 = b"ID3fake speech"


class FakeServers:

    def __init__(self):
        self.comfy_up = True
        self.prompt_reply = (200, {"prompt_id": "p1"})
        self.polls_before_done = 1
        self.history_failures = 0
        self.outputs = {"9": {"images": [{"filename": "starfire_0001.png", "subfolder": "", "type": "output"}]}}
        self.files = {"starfire_0001.png": PNG}
        self.openai_replies = {}
        self.queued = []
        self.openai_requests = []
        self.polls = 0

    def handle(self, request):
        if request.url.host == "comfy.test":
            return self.comfy(request)
        if request.url.host == "api.openai.com":
            self.openai_requests.append((request.url.path, request.headers.get("authorization"), json.loads(request.content)))
            reply = self.openai_replies.get(request.url.path)
            if reply == "unreachable":
                raise httpx.ConnectError("connection refused", request=request)
            status, body = reply
            return httpx.Response(status, content=body) if isinstance(body, bytes) else httpx.Response(status, json=body)
        raise httpx.ConnectError("unexpected host", request=request)

    def comfy(self, request):
        if not self.comfy_up:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path
        if path == "/system_stats":
            return httpx.Response(200, json={"system": {}})
        if path == "/prompt":
            self.queued.append(json.loads(request.content)["prompt"])
            status, body = self.prompt_reply
            return httpx.Response(status, json=body) if isinstance(body, dict) else httpx.Response(status, text=body)
        if path.startswith("/history/"):
            if self.history_failures:
                self.history_failures -= 1
                return httpx.Response(502, text="busy")
            self.polls += 1
            if self.polls_before_done is None or self.polls < self.polls_before_done:
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"p1": {"outputs": self.outputs}})
        if path == "/view":
            name = parse_qs(request.url.query.decode())["filename"][0]
            return httpx.Response(200, content=self.files[name]) if name in self.files else httpx.Response(404)
        return httpx.Response(404)


@pytest.fixture
def servers(monkeypatch):
    fake = FakeServers()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *args, **kwargs: real_client(*args, transport=httpx.MockTransport(fake.handle), **kwargs))
    monkeypatch.setattr(comfyui_client, "POLL_INTERVAL", 0.001)
    return fake


@pytest.fixture
def ctx(tmp_path):
    data_dir = str(tmp_path)
    return ToolContext(
        memory=MemoryStore(data_dir), documents=DocumentStore(data_dir), tasks=TaskStore(data_dir),
        task_runs=TaskRunStore(data_dir), email_accounts=EmailAccountStore(data_dir),
        api_keys=APIKeyManager(data_dir), notes=NoteStore(data_dir), endpoints=ModelEndpointStore(data_dir),
        generated_files=GeneratedFileStore(data_dir), comfyui_config=ComfyUIConfigStore(data_dir),
        piper_config=PiperConfigStore(data_dir), custom_workflows=CustomWorkflowStore(data_dir),
    )


@pytest.fixture
def local_comfy(ctx):
    ctx.comfyui_config.update(base_url=COMFY, default_checkpoint="dreamshaper.safetensors", default_negative_prompt="blurry")
    return ctx


@pytest.fixture
def openai_key(ctx):
    endpoint = ctx.endpoints.add("https://api.openai.com/v1", "api-key")
    ctx.api_keys.save(endpoint.id, "sk-test-not-real")
    return ctx


def call(name, arguments, ctx):
    return asyncio.run(builtin_tools.call_builtin_tool(name, arguments, ctx))


def saved_bytes(ctx, result):
    entry = ctx.generated_files.get(json.loads(result)["id"])
    with open(ctx.generated_files.path_for(entry), "rb") as f:
        return entry, f.read()


def test_a_picture_is_made_on_the_local_comfyui_with_the_quality_asked_for(servers, local_comfy):
    result = call("generate_image", {"prompt": "a lighthouse at dusk", "quality": "high"}, local_comfy)
    entry, data = saved_bytes(local_comfy, result)
    assert json.loads(result) == {"kind": "image", "id": entry.id, "url": f"/api/generated/{entry.id}", "prompt": "a lighthouse at dusk"}
    assert data == PNG
    assert entry.source == "a lighthouse at dusk"
    [workflow] = servers.queued
    assert workflow["6"]["inputs"]["text"] == "a lighthouse at dusk"
    assert workflow["7"]["inputs"]["text"] == "blurry"
    assert workflow["4"]["inputs"]["ckpt_name"] == "dreamshaper.safetensors"
    assert (workflow["5"]["inputs"]["width"], workflow["3"]["inputs"]["steps"]) == (1024, 35)
    assert servers.openai_requests == []


def test_a_comfyui_that_never_finishes_reports_a_timeout(servers, local_comfy, monkeypatch):
    servers.polls_before_done = None
    monkeypatch.setattr(comfyui_client, "GENERATE_TIMEOUT", 0.6)
    result = call("generate_image", {"prompt": "a lighthouse"}, local_comfy)
    assert result == "error: ComfyUI generation timed out after 1s"
    assert local_comfy.generated_files.list() == []


def test_a_busy_comfyui_history_is_asked_again_until_the_picture_is_ready(servers, local_comfy, caplog):
    servers.history_failures = 2
    with caplog.at_level(logging.DEBUG, logger="comfyui_client"):
        result = call("generate_image", {"prompt": "a lighthouse"}, local_comfy)
    assert saved_bytes(local_comfy, result)[1] == PNG
    assert caplog.text.count("could not read ComfyUI history") == 2


def test_a_workflow_comfyui_rejects_reports_its_reason(servers, local_comfy):
    servers.prompt_reply = (400, "{\"error\": \"ckpt_name not in list\"}")
    result = call("generate_image", {"prompt": "a lighthouse"}, local_comfy)
    assert result == "error: ComfyUI rejected the workflow (400): {\"error\": \"ckpt_name not in list\"}"


def test_a_queue_reply_without_a_prompt_id_is_reported(servers, local_comfy):
    servers.prompt_reply = (200, {"number": 3})
    assert call("generate_image", {"prompt": "x"}, local_comfy) == \
        "error: unexpected response queuing the workflow: missing 'prompt_id'"


def test_a_finished_workflow_with_no_picture_is_reported(servers, local_comfy):
    servers.outputs = {"9": {"text": ["done"]}, "10": "not a node output"}
    assert call("generate_image", {"prompt": "x"}, local_comfy) == \
        "error: ComfyUI finished the workflow but produced no image output"


def test_without_comfyui_or_an_openai_key_the_model_is_told_what_to_set_up(servers, ctx):
    servers.comfy_up = False
    result = call("generate_image", {"prompt": "x"}, ctx)
    assert result.startswith("error: no local ComfyUI configured")
    assert "no OpenAI endpoint" in result


def test_a_missing_prompt_asks_for_one(servers, ctx):
    assert call("generate_image", {}, ctx) == "error: prompt is required"
    assert call("generate_audio", {"text": ""}, ctx) == "error: text is required"


def test_without_comfyui_the_picture_comes_from_openai_with_the_stored_key(servers, local_comfy, openai_key):
    servers.comfy_up = False
    servers.openai_replies["/v1/images/generations"] = (200, {"data": [{"b64_json": base64.b64encode(PNG).decode()}]})
    result = call("generate_image", {"prompt": "a lighthouse", "size": "1792x1024"}, openai_key)
    assert saved_bytes(openai_key, result)[1] == PNG
    [(path, auth, body)] = servers.openai_requests
    assert auth == "Bearer sk-test-not-real"
    assert (body["prompt"], body["size"], body["response_format"]) == ("a lighthouse", "1792x1024", "b64_json")


def test_an_openai_refusal_reports_the_status_and_reason(servers, openai_key):
    servers.comfy_up = False
    servers.openai_replies["/v1/images/generations"] = (400, {"error": {"message": "rejected by the safety system"}})
    result = call("generate_image", {"prompt": "x"}, openai_key)
    assert result.startswith("error: image generation failed (400): ")
    assert "rejected by the safety system" in result


def test_an_openai_answer_without_a_picture_is_reported(servers, openai_key):
    servers.comfy_up = False
    servers.openai_replies["/v1/images/generations"] = (200, {"data": []})
    assert call("generate_image", {"prompt": "x"}, openai_key).startswith("error: unexpected response shape from image API")


def test_speech_without_piper_comes_from_openai_and_an_unknown_voice_falls_back_to_alloy(servers, openai_key, monkeypatch):
    monkeypatch.setattr(piper_tts, "is_installed", lambda: False)
    servers.openai_replies["/v1/audio/speech"] = (200, MP3)
    result = call("generate_audio", {"text": "Hello there", "voice": "darth"}, openai_key)
    entry, data = saved_bytes(openai_key, result)
    assert (entry.filename, data) == ("generated.mp3", MP3)
    assert servers.openai_requests[0][2] == {"model": "tts-1", "input": "Hello there", "voice": "alloy"}


def test_speech_reports_an_openai_that_cannot_be_reached(servers, openai_key, monkeypatch):
    monkeypatch.setattr(piper_tts, "is_installed", lambda: False)
    servers.openai_replies["/v1/audio/speech"] = "unreachable"
    assert call("generate_audio", {"text": "Hello"}, openai_key) == \
        "error: audio generation request failed: connection refused"


def test_speech_reports_an_openai_error_status(servers, openai_key, monkeypatch):
    monkeypatch.setattr(piper_tts, "is_installed", lambda: False)
    servers.openai_replies["/v1/audio/speech"] = (429, {"error": "slow down"})
    assert call("generate_audio", {"text": "Hello"}, openai_key).startswith("error: audio generation failed (429): ")


def test_without_piper_or_an_openai_key_the_model_is_told_what_to_set_up(servers, ctx, monkeypatch):
    monkeypatch.setattr(piper_tts, "is_installed", lambda: False)
    assert call("generate_audio", {"text": "Hello"}, ctx).startswith("error: no local Piper voice configured")


def test_speech_uses_the_local_piper_voice_when_one_is_set_up(servers, openai_key, monkeypatch, tmp_path):
    voice = tmp_path / "en_GB-alan-medium.onnx"
    voice.write_bytes(b"voice")
    openai_key.piper_config.update(voice_model_path=str(voice))
    spoken = []

    async def fake_piper(text, voice_model_path):
        spoken.append((text, voice_model_path))
        return b"RIFFfake wav"

    monkeypatch.setattr(piper_tts, "is_installed", lambda: True)
    monkeypatch.setattr(piper_tts, "generate", fake_piper)
    result = call("generate_audio", {"text": "Hello"}, openai_key)
    entry, data = saved_bytes(openai_key, result)
    assert (entry.filename, data) == ("generated.wav", b"RIFFfake wav")
    assert spoken == [("Hello", str(voice))]
    assert servers.openai_requests == []


def test_a_piper_failure_is_reported_without_falling_back_to_openai(servers, openai_key, monkeypatch, tmp_path):
    openai_key.piper_config.update(voice_model_path=str(tmp_path / "voice.onnx"))

    async def failing_piper(text, voice_model_path):
        raise RuntimeError("piper failed: voice file is damaged")

    monkeypatch.setattr(piper_tts, "is_installed", lambda: True)
    monkeypatch.setattr(piper_tts, "generate", failing_piper)
    assert call("generate_audio", {"text": "Hello"}, openai_key) == "error: piper failed: voice file is damaged"
    assert servers.openai_requests == []


VIDEO_WORKFLOW = {"12": {"class_type": "CLIPTextEncode", "inputs": {"text": "placeholder"}}}


def test_a_saved_video_workflow_runs_with_the_prompt_and_keeps_every_file(servers, local_comfy):
    local_comfy.custom_workflows.add("Short clip", VIDEO_WORKFLOW, "12")
    servers.outputs = {"20": {"gifs": [{"filename": "clip_0001.mp4", "type": "output"}],
                              "images": [{"filename": "frame_0001.png", "type": "output"}]}}
    servers.files = {"clip_0001.mp4": MP4, "frame_0001.png": PNG}
    result = json.loads(call("generate_video", {"workflow_name": "Short clip", "prompt": "waves at night"}, local_comfy))
    assert [(f["kind"], f["filename"]) for f in result["files"]] == [("video", "clip_0001.mp4"), ("image", "frame_0001.png")]
    assert servers.queued[0]["12"]["inputs"]["text"] == "waves at night"
    stored = {e.filename: e.content_type for e in local_comfy.generated_files.list()}
    assert stored == {"clip_0001.mp4": "video/mp4", "frame_0001.png": "image/png"}


def test_an_unknown_video_workflow_lists_the_ones_there_are(servers, local_comfy):
    local_comfy.custom_workflows.add("Short clip", VIDEO_WORKFLOW, "12")
    assert call("generate_video", {"workflow_name": "Long film", "prompt": "x"}, local_comfy) == \
        "error: no saved workflow named 'Long film'. Available: ['Short clip']"


def test_a_video_workflow_needs_a_name_and_a_prompt(servers, local_comfy):
    assert call("generate_video", {"workflow_name": "Short clip"}, local_comfy) == "error: workflow_name and prompt are required"


def test_a_video_when_comfyui_is_down_says_where_it_looked(servers, local_comfy):
    local_comfy.custom_workflows.add("Short clip", VIDEO_WORKFLOW, "12")
    servers.comfy_up = False
    assert call("generate_video", {"workflow_name": "Short clip", "prompt": "x"}, local_comfy) == \
        f"error: ComfyUI is not reachable at {COMFY}"


def test_a_video_workflow_whose_prompt_node_is_gone_says_so(servers, local_comfy):
    local_comfy.custom_workflows.add("Short clip", VIDEO_WORKFLOW, "99")
    result = call("generate_video", {"workflow_name": "Short clip", "prompt": "x"}, local_comfy)
    assert result.startswith("error: workflow has no node '99' with input 'text'")
    assert servers.queued == []


def test_comfyui_checkpoints_are_listed_and_a_strange_answer_lists_none(servers, monkeypatch, caplog):
    real_handle = servers.handle

    def with_checkpoints(request):
        if request.url.path == "/object_info/CheckpointLoaderSimple":
            return httpx.Response(200, json={"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["a.safetensors", "b.ckpt"]]}}}})
        return real_handle(request)

    servers.handle = with_checkpoints
    assert asyncio.run(comfyui_client.list_checkpoints(COMFY)) == ["a.safetensors", "b.ckpt"]
    servers.handle = lambda request: httpx.Response(200, json={"unexpected": True})
    with caplog.at_level(logging.WARNING, logger="comfyui_client"):
        assert asyncio.run(comfyui_client.list_checkpoints(COMFY)) == []
    assert "could not list ComfyUI checkpoints" in caplog.text


def pull(url, folder, name):
    async def collect():
        return [progress async for progress in comfyui_client.pull_checkpoint(url, folder, name)]
    return asyncio.run(collect())


def test_a_checkpoint_download_reports_progress_and_lands_in_the_folder(servers, tmp_path):
    servers.handle = lambda request: httpx.Response(200, content=b"x" * 3000, headers={"content-length": "3000"})
    progress = pull("https://models.example.test/m.safetensors", str(tmp_path), "m.safetensors")
    assert progress[-1] == (3000, 3000)
    assert (tmp_path / "m.safetensors").read_bytes() == b"x" * 3000
    assert not (tmp_path / "m.safetensors.part").exists()


def test_a_checkpoint_download_into_a_missing_folder_is_refused(servers, tmp_path):
    with pytest.raises(RuntimeError, match="checkpoints directory does not exist"):
        pull("https://models.example.test/m.safetensors", str(tmp_path / "missing"), "m.safetensors")


def test_a_checkpoint_that_is_not_found_leaves_no_file_behind(servers, tmp_path):
    servers.handle = lambda request: httpx.Response(404)
    with pytest.raises(httpx.HTTPStatusError):
        pull("https://models.example.test/gone.safetensors", str(tmp_path), "gone.safetensors")
    assert list(tmp_path.iterdir()) == []


DOCUMENT = "# Trip plan\n\n## Day one\n- Train at 9\n- Lunch in Café Nero\n\nBring a coat. Ünïcödé ✓"


def test_a_pdf_document_is_a_real_pdf_even_with_characters_its_font_lacks(ctx):
    result = json.loads(call("generate_document", {"content": DOCUMENT, "format": "pdf", "filename": "trip"}, ctx))
    entry, data = saved_bytes(ctx, json.dumps(result))
    assert (result["filename"], entry.content_type) == ("trip.pdf", "application/pdf")
    assert data.startswith(b"%PDF-") and data.rstrip().endswith(b"%%EOF")


def test_a_word_document_keeps_headings_bullets_and_text(ctx):
    import io
    import docx

    result = json.loads(call("generate_document", {"content": DOCUMENT, "format": "docx", "filename": "trip"}, ctx))
    data = saved_bytes(ctx, json.dumps(result))[1]
    paragraphs = [(p.style.name, p.text) for p in docx.Document(io.BytesIO(data)).paragraphs if p.text]
    assert paragraphs == [
        ("Heading 1", "Trip plan"), ("Heading 2", "Day one"), ("List Bullet", "Train at 9"),
        ("List Bullet", "Lunch in Café Nero"), ("Normal", "Bring a coat. Ünïcödé ✓"),
    ]


def test_a_document_that_cannot_be_built_reports_why(ctx, monkeypatch):
    import media_generation

    def broken(content, fmt):
        raise OSError("no space left on device")

    monkeypatch.setattr(media_generation, "generate_document", broken)
    assert call("generate_document", {"content": "x", "format": "pdf"}, ctx) == \
        "error: could not generate document: no space left on device"
