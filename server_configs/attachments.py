from __future__ import annotations

import base64
import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from urllib.parse import quote

import httpx

from atomic_io import atomic_write_json
from providers import _detect_provider, build_headers, build_models_url

KINDS = ("image", "audio", "video")
MAX_BYTES = {"image": 20 * 1024 * 1024, "audio": 300 * 1024 * 1024, "video": 300 * 1024 * 1024}
KEEP_SECONDS = 30 * 24 * 3600
REMOTE_ID_LIFETIME_SECONDS = 20 * 3600
UPLOAD_TIMEOUT_SECONDS = 300
CHUNK_BYTES = 1024 * 1024
ID_RE = re.compile(r"^[0-9a-f]{32}$")


class AttachmentError(ValueError):
    pass


class AttachmentNoRoom(AttachmentError):
    pass


@dataclass
class Attachment:
    id: str
    name: str
    mime: str
    kind: str
    size: int
    created: float
    remote_ids: dict = field(default_factory=dict)

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "mime": self.mime, "kind": self.kind, "size": self.size}


def kind_of(mime: str) -> str | None:
    family = (mime or "").split("/", 1)[0].lower()
    return family if family in KINDS else None


def safe_name(name: str) -> str:
    cleaned = re.sub(r"[\x00-\x1f/\\]", "_", os.path.basename(name or "")).strip() or "attachment"
    return cleaned[:200]


class AttachmentStore:

    def __init__(self, data_dir: str):
        self.root = os.path.join(data_dir, "attachments")
        os.makedirs(self.root, exist_ok=True)

    def _paths(self, attachment_id: str) -> tuple[str, str]:
        if not ID_RE.match(attachment_id or ""):
            raise AttachmentError("bad attachment id")
        base = os.path.join(self.root, attachment_id)
        return base, base + ".json"

    def used_bytes(self) -> int:
        return sum(entry.stat().st_size for entry in os.scandir(self.root) if entry.is_file())

    async def save(self, name: str, mime: str, chunks, room_bytes: int | None = None) -> Attachment:
        kind = kind_of(mime)
        if not kind:
            raise AttachmentError(f"{mime or 'this file type'} can't be attached - use images, audio or video")
        attachment = Attachment(id=uuid.uuid4().hex, name=safe_name(name), mime=mime.lower(), kind=kind,
                                size=0, created=time.time())
        data_path, meta_path = self._paths(attachment.id)
        limit = MAX_BYTES[kind]
        try:
            with open(data_path, "wb") as out:
                async for chunk in chunks:
                    attachment.size += len(chunk)
                    if attachment.size > limit:
                        raise AttachmentError(f"{attachment.name} is over the {limit // (1024 * 1024)} MB limit "
                                              f"for {kind}")
                    if room_bytes is not None and attachment.size > room_bytes:
                        raise AttachmentNoRoom(f"there's no room left for {attachment.name}")
                    out.write(chunk)
        except BaseException:
            if os.path.exists(data_path):
                os.remove(data_path)
            raise
        if not attachment.size:
            os.remove(data_path)
            raise AttachmentError(f"{attachment.name} is empty")
        atomic_write_json(meta_path, asdict(attachment))
        return attachment

    def get(self, attachment_id: str) -> Attachment:
        _, meta_path = self._paths(attachment_id)
        if not os.path.exists(meta_path):
            raise AttachmentError("that attachment has expired - attach the file again")
        with open(meta_path, "r", encoding="utf-8") as f:
            return Attachment(**json.load(f))

    def data_path(self, attachment_id: str) -> str:
        return self._paths(attachment_id)[0]

    def read_bytes(self, attachment: Attachment) -> bytes:
        with open(self.data_path(attachment.id), "rb") as f:
            return f.read()

    def remember_remote_id(self, attachment: Attachment, base_url: str, remote_id: str) -> None:
        attachment.remote_ids[base_url] = {"id": remote_id, "at": time.time()}
        atomic_write_json(self._paths(attachment.id)[1], asdict(attachment))

    def prune(self, now: float | None = None) -> int:
        now = now or time.time()
        removed = 0
        for entry in os.listdir(self.root):
            if not entry.endswith(".json"):
                continue
            attachment_id = entry[:-5]
            try:
                attachment = self.get(attachment_id)
            except (AttachmentError, ValueError, OSError, TypeError):
                continue
            if now - attachment.created < KEEP_SECONDS:
                continue
            for path in self._paths(attachment_id):
                if os.path.exists(path):
                    os.remove(path)
            removed += 1
        return removed


def files_url(base_url: str) -> str:
    return build_models_url(base_url)[: -len("/models")] + "/files"


async def _file_chunks(path: str):
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK_BYTES):
            yield chunk


async def upload_to_endpoint(store: AttachmentStore, attachment: Attachment, base_url: str,
                             api_key: str | None, label: str) -> str:
    cached = attachment.remote_ids.get(base_url)
    if cached and time.time() - cached["at"] < REMOTE_ID_LIFETIME_SECONDS:
        return cached["id"]
    headers = {**build_headers(api_key, base_url), "Content-Type": attachment.mime,
               "X-Filename": quote(attachment.name)}
    headers["Content-Length"] = str(attachment.size)
    async with httpx.AsyncClient(timeout=httpx.Timeout(UPLOAD_TIMEOUT_SECONDS, connect=10.0)) as client:
        response = await client.post(files_url(base_url), headers=headers,
                                     content=_file_chunks(store.data_path(attachment.id)))
    if response.status_code in (404, 405):
        raise AttachmentError(f"{label} can't take {attachment.kind} files - send them to Arty instead")
    if response.is_error:
        raise AttachmentError(f"{label} rejected {attachment.name}: {response.status_code} {response.text[:200]}")
    remote_id = response.json().get("id")
    if not remote_id:
        raise AttachmentError(f"{label} didn't return an id for {attachment.name}")
    store.remember_remote_id(attachment, base_url, remote_id)
    return remote_id


def _image_part(provider: str, attachment: Attachment, encoded: str) -> dict:
    if provider == "anthropic":
        return {"type": "image", "source": {"type": "base64", "media_type": attachment.mime, "data": encoded}}
    return {"type": "image_url", "image_url": {"url": f"data:{attachment.mime};base64,{encoded}"}}


async def expand_attachments(messages: list[dict], store: AttachmentStore, base_url: str,
                             api_key: str | None, label: str) -> list[dict]:
    provider = _detect_provider(base_url)
    expanded = []
    for message in messages:
        ids = message.get("attachments") or []
        plain = {k: v for k, v in message.items() if k != "attachments"}
        if not ids:
            expanded.append(plain)
            continue
        text = plain.get("content") or ""
        images, parts = [], []
        for attachment_id in ids:
            attachment = store.get(attachment_id)
            if attachment.kind == "image":
                encoded = base64.b64encode(store.read_bytes(attachment)).decode()
                if provider == "ollama":
                    images.append(encoded)
                else:
                    parts.append(_image_part(provider, attachment, encoded))
                continue
            if provider != "openai":
                raise AttachmentError(f"{label} can't take {attachment.kind} files - send them to Arty instead")
            remote_id = await upload_to_endpoint(store, attachment, base_url, api_key, label)
            parts.append({"type": "file", "file": {"file_id": remote_id, "filename": attachment.name}})
        if images:
            plain["images"] = images
        if parts:
            plain["content"] = [{"type": "text", "text": text}] + parts
        expanded.append(plain)
    return expanded
