from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, Field, StrictBool

from arty_devices import API_SUFFIX, ask_brain, require_admin

router = APIRouter(prefix="/api/arty-cli")

MAX_WAIT_SECONDS = 20
WAIT_MARGIN_SECONDS = 10
MAX_MESSAGE_CHARS = 8000
MAX_REQUEST_ID_CHARS = 128
SESSION_ID = r"^[0-9a-f]{16}$"
PASSED_ON = frozenset({400, 404, 409, 410, 413, 429})

SessionId = Path(pattern=SESSION_ID)


class MessageBody(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class AnswerBody(BaseModel):
    request_id: str = Field(min_length=1, max_length=MAX_REQUEST_ID_CHARS)
    allow: StrictBool


def session_path(session_id: str, action: str = "") -> str:
    return f"{API_SUFFIX}/remote/sessions/{session_id}" + (f"/{action}" if action else "")


async def relay(method: str, path: str, **details) -> dict:
    return await ask_brain(method, path, passed_on=PASSED_ON, **details)


@router.get("/sessions")
async def sessions(request: Request):
    require_admin(request)
    answer = await relay("GET", f"{API_SUFFIX}/remote/sessions")
    return {"sessions": answer.get("sessions", [])}


@router.get("/sessions/{session_id}/events")
async def events(request: Request, session_id: str = SessionId, after: int = Query(0, ge=0),
                 wait: float = Query(0, ge=0)):
    require_admin(request)
    capped = min(wait, MAX_WAIT_SECONDS)
    return await relay("GET", session_path(session_id, "events"), params={"after": after, "wait": int(capped)},
                       timeout=capped + WAIT_MARGIN_SECONDS)


@router.post("/sessions/{session_id}/messages")
async def message(request: Request, body: MessageBody, session_id: str = SessionId):
    require_admin(request)
    await relay("POST", session_path(session_id, "messages"), json_body={"text": body.text})
    return {"ok": True}


@router.post("/sessions/{session_id}/answers")
async def answer(request: Request, body: AnswerBody, session_id: str = SessionId):
    require_admin(request)
    await relay("POST", session_path(session_id, "answers"), json_body={"request_id": body.request_id, "allow": body.allow})
    return {"ok": True}


@router.post("/sessions/{session_id}/interrupt")
async def interrupt(request: Request, session_id: str = SessionId):
    require_admin(request)
    await relay("POST", session_path(session_id, "interrupt"), json_body={})
    return {"ok": True}
