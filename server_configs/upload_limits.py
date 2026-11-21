from __future__ import annotations

import shutil

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

KIB = 1024
MIB = 1024 * 1024
ATTACHMENT_BODY_MB = 301
DOCUMENT_PATH = "/api/documents"
ATTACHMENT_PATH = "/api/attachments"
RESTORE_PATH = "/api/restore"
CHAT_IMPORT_PATH = "/api/chat/sessions/import"
BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DEFAULT_BODY_BYTES = 32 * MIB
CHAT_IMPORT_BODY_BYTES = 128 * MIB
SMALL_BODY_PREFIXES = (
    ("/api/auth/", 64 * KIB),
    ("/api/memory", 256 * KIB),
    ("/api/code-check", 1 * MIB),
    ("/api/notes", 8 * MIB),
    ("/v1/", 16 * MIB),
)


def room_on_disk(path: str, reserve_mb: int) -> int:
    return shutil.disk_usage(path).free - reserve_mb * MIB


def require_room(path: str, needed_bytes: int, reserve_mb: int, what: str) -> None:
    if needed_bytes > room_on_disk(path, reserve_mb):
        raise HTTPException(507, f"there isn't enough free disk space for {what} - free some space and try again")


def too_large(what: str, limit_bytes: int) -> HTTPException:
    return HTTPException(413, f"{what} is over the {limit_bytes // MIB} MB limit")


def body_caps(settings) -> dict[str, int]:
    return {
        DOCUMENT_PATH: (settings.max_document_mb + 1) * MIB,
        ATTACHMENT_PATH: ATTACHMENT_BODY_MB * MIB,
        RESTORE_PATH: (settings.max_restore_mb + 1) * MIB,
    }


def request_body_cap(path: str) -> int:
    if path == CHAT_IMPORT_PATH:
        return CHAT_IMPORT_BODY_BYTES
    return next((cap for prefix, cap in SMALL_BODY_PREFIXES if path.startswith(prefix)), DEFAULT_BODY_BYTES)


def _declared_length(scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


TOO_LARGE = (413, "that upload is too large")
REQUEST_TOO_LARGE = (413, "that request is too large")
NO_ROOM = (507, "there isn't enough free disk space for that upload - free some space and try again")


class LimitUploadBodies:

    def __init__(self, app, settings):
        self.app = app
        self.settings = settings

    def _refusal(self, size: int, cap: int, room: int | None) -> tuple[int, str] | None:
        is_upload = room is not None
        if is_upload and size > room:
            return NO_ROOM
        if size > cap:
            return TOO_LARGE if is_upload else REQUEST_TOO_LARGE
        return None

    def _limits(self, scope) -> tuple[int, int | None] | None:
        if scope["type"] != "http" or scope["method"] not in BODY_METHODS:
            return None
        upload_cap = body_caps(self.settings).get(scope["path"]) if scope["method"] == "POST" else None
        if upload_cap is None:
            return request_body_cap(scope["path"]), None
        return upload_cap, room_on_disk(self.settings.data_dir, self.settings.min_free_disk_mb)

    async def __call__(self, scope, receive, send):
        limits = self._limits(scope)
        if limits is None:
            await self.app(scope, receive, send)
            return
        cap, room = limits
        refusal = self._refusal(_declared_length(scope) or 0, cap, room)
        if refusal:
            status, detail = refusal
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)
            return
        received = 0

        async def counted_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            over = self._refusal(received, cap, room)
            if over:
                raise HTTPException(*over)
            return message

        await self.app(scope, counted_receive, send)


def install(app: FastAPI, settings) -> None:
    app.add_middleware(LimitUploadBodies, settings=settings)
