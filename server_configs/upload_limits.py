from __future__ import annotations

import shutil

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

MIB = 1024 * 1024
ATTACHMENT_BODY_MB = 301
DOCUMENT_PATH = "/api/documents"
ATTACHMENT_PATH = "/api/attachments"
RESTORE_PATH = "/api/restore"


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


def _declared_length(scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


TOO_LARGE = (413, "that upload is too large")
NO_ROOM = (507, "there isn't enough free disk space for that upload - free some space and try again")


class LimitUploadBodies:

    def __init__(self, app, settings):
        self.app = app
        self.settings = settings

    def _refusal(self, size: int, cap: int, room: int) -> tuple[int, str] | None:
        if size > room:
            return NO_ROOM
        if size > cap:
            return TOO_LARGE
        return None

    async def __call__(self, scope, receive, send):
        cap = body_caps(self.settings).get(scope["path"]) if scope["type"] == "http" else None
        if cap is None or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        room = room_on_disk(self.settings.data_dir, self.settings.min_free_disk_mb)
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
