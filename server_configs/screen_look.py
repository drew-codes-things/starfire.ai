from __future__ import annotations

import asyncio
import base64
import binascii
import time
import uuid

LOOK_TIMEOUT_SECONDS = 180
MAX_APP_CHARS = 80
MAX_IMAGE_BASE64_CHARS = 6_000_000
MAX_DETAIL_CHARS = 300
IMAGE_SIGNATURES = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n"}
REASON_TEXTS = {
    "declined": "the user said no in Arty Desktop",
    "timed_out": "nobody allowed it in Arty Desktop in time",
    "not_running": "Arty Desktop is not running on their computer",
    "not_linked": "starfire Desktop is not linked to Arty Desktop",
    "not_found": "no window for that app was found",
    "unavailable": "computer control is turned off in Arty Desktop",
    "cannot_see": "this computer cannot take screenshots",
    "off_limits": "that is a window that is never looked at",
    "busy": "Arty Desktop was already waiting on another step",
    "ambiguous": "more than one window matches that name, so none was picked; ask for the window by its full title",
    "changed": "the window changed after the user was asked, so it was not captured",
    "failed": "the screenshot failed",
}
DEFAULT_REASON_TEXT = "it failed for a reason starfire Desktop could not name"

_pending: dict[str, dict] = {}


class NotWaiting(LookupError):
    pass


class AlreadyClaimed(LookupError):
    pass


class BadPicture(ValueError):
    pass


def stage(app: str, owner: str | None) -> str:
    request_id = uuid.uuid4().hex[:16]
    _pending[request_id] = {"app": app, "owner": owner, "claimed": False, "created": time.time(),
                            "future": asyncio.get_running_loop().create_future()}
    return request_id


def _own_entry(request_id: str, owner: str | None) -> dict:
    entry = _pending.get(request_id)
    if entry is None or entry["owner"] != owner or entry["future"].done():
        raise NotWaiting(request_id)
    return entry


def claim(request_id: str, owner: str | None) -> str:
    entry = _own_entry(request_id, owner)
    if entry["claimed"]:
        raise AlreadyClaimed(request_id)
    entry["claimed"] = True
    return entry["app"]


def _checked_picture(outcome: dict) -> None:
    mime, encoded = outcome.get("mime", ""), outcome.get("image", "")
    signature = IMAGE_SIGNATURES.get(mime)
    if signature is None:
        raise BadPicture("the picture must be a JPEG or a PNG")
    if not encoded or len(encoded) > MAX_IMAGE_BASE64_CHARS:
        raise BadPicture("the picture is missing or too large")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise BadPicture("the picture is not valid base64")
    if not data.startswith(signature):
        raise BadPicture("the picture is not the kind of file it says it is")


def resolve(request_id: str, owner: str | None, outcome: dict) -> bool:
    try:
        entry = _own_entry(request_id, owner)
    except NotWaiting:
        return False
    if outcome.get("ok") is True:
        _checked_picture(outcome)
    entry["future"].set_result(outcome)
    return True


async def wait_for_look(request_id: str) -> dict:
    entry = _pending.get(request_id)
    if entry is None:
        return {"ok": False, "reason": "timed_out"}
    try:
        return await asyncio.wait_for(asyncio.shield(entry["future"]), timeout=LOOK_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return {"ok": False, "reason": "timed_out"}
    finally:
        _pending.pop(request_id, None)


def image_message(provider: str, image: dict) -> dict:
    caption, mime, encoded = image["caption"], image["mime"], image["image"]
    if provider == "ollama":
        return {"role": "user", "content": caption, "images": [encoded]}
    if provider == "anthropic":
        part = {"type": "image", "source": {"type": "base64", "media_type": mime, "data": encoded}}
    else:
        part = {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}
    return {"role": "user", "content": [{"type": "text", "text": caption}, part]}
