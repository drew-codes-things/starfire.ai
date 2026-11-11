import json
import logging
import re

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, StrictBool

from api_key_manager import APIKeyManager
from config import config
from model_endpoints import ModelEndpointStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/arty-devices")
endpoints = ModelEndpointStore(config.data_dir)
api_keys = APIKeyManager(config.data_dir)
brain_transport: httpx.AsyncBaseTransport | None = None

BRAIN_TIMEOUT_SECONDS = 10.0
API_SUFFIX = "/v1"
SETUP_CODE_VERSION = 1
MAX_NAME_CHARS = 80
CODE_HALF = 4
CODE_LETTERS = re.compile(r"^[A-HJ-NP-Z2-9]{8}$")
SEPARATORS = re.compile(r"[\s-]")
REFUSED = {401, 403}
PASSED_ON = {404, 429}
NO_ADDRESS = "Set ARTY_BASE_URL in starfire's .env to Arty's address (for example http://<arty-host>:8081/v1), then restart starfire."
NO_KEY = "Add Arty under Settings > Connections with the same address as ARTY_BASE_URL and Arty's API key."
CODE_RAN_OUT = "That code has run out or was never made. Start again on the computer."


class ApproveBody(BaseModel):
    code: str = Field(max_length=32)
    approve: StrictBool


class KeyBody(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME_CHARS)


def normalised_code(raw: str) -> str:
    letters = SEPARATORS.sub("", str(raw or "")).upper()
    if not CODE_LETTERS.fullmatch(letters):
        raise HTTPException(400, "That isn't an approval code: it's 8 letters and numbers like ABCD-EFGH.")
    return f"{letters[:CODE_HALF]}-{letters[CODE_HALF:]}"


def brain_address() -> str:
    return config.arty_base_url.strip().rstrip("/")


def missing_setup() -> str:
    address = brain_address()
    if not address:
        return NO_ADDRESS
    endpoint = endpoints.find_by_url(address)
    if endpoint is None or not api_keys.load().get(endpoint.id):
        return NO_KEY
    return ""


def brain_key() -> str:
    problem = missing_setup()
    if problem:
        raise HTTPException(503, problem)
    return api_keys.load()[endpoints.find_by_url(brain_address()).id]


def require_admin(request: Request) -> None:
    user = getattr(request.state, "user", None)
    if user is None or user.role != "admin":
        raise HTTPException(403, "only an admin can manage Arty's devices")


def brain_message(response: httpx.Response) -> str:
    try:
        answer = response.json()
    except ValueError:
        return ""
    error = answer.get("error") if isinstance(answer, dict) else None
    message = error.get("message") if isinstance(error, dict) else error
    return message if isinstance(message, str) else ""


async def ask_brain(method: str, path: str, json_body: dict | None = None, params: dict | None = None) -> dict:
    key = brain_key()
    address = brain_address()
    try:
        async with httpx.AsyncClient(timeout=BRAIN_TIMEOUT_SECONDS, transport=brain_transport) as client:
            response = await client.request(method, f"{address.removesuffix(API_SUFFIX)}{path}", json=json_body,
                                            params=params, headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as error:
        logger.warning("Arty didn't answer %s %s: %s", method, path, error)
        raise HTTPException(502, f"Arty isn't answering at {address}. Check it's running.") from error
    if response.status_code in REFUSED:
        raise HTTPException(502, f"Arty refused starfire's key. {NO_KEY}")
    if response.status_code in PASSED_ON:
        raise HTTPException(response.status_code, brain_message(response) or CODE_RAN_OUT)
    if response.status_code >= 400:
        raise HTTPException(502, f"Arty answered {response.status_code}: {brain_message(response) or 'no details'}")
    try:
        return response.json()
    except ValueError as error:
        raise HTTPException(502, "Arty sent a reply starfire doesn't understand.") from error


def phone_setup_code(key: str) -> str:
    address = (config.arty_phone_url.strip() or brain_address()).rstrip("/").removesuffix(API_SUFFIX)
    code = {"arty": SETUP_CODE_VERSION, "url": address, "key": key}
    access_id, access_secret = config.arty_access_client_id.strip(), config.arty_access_client_secret.strip()
    if access_id and access_secret:
        code |= {"id": access_id, "secret": access_secret}
    return json.dumps(code, separators=(",", ":"))


@router.get("/status")
async def status(request: Request):
    require_admin(request)
    problem = missing_setup()
    return {"ready": not problem, "message": problem}


@router.get("/pending")
async def pending(request: Request, code: str = Query("", max_length=32)):
    require_admin(request)
    wanted = normalised_code(code)
    try:
        return await ask_brain("GET", f"{API_SUFFIX}/device/pending", params={"code": wanted})
    except HTTPException as error:
        if error.status_code == 404:
            raise HTTPException(404, CODE_RAN_OUT) from error
        raise


@router.post("/approve")
async def approve(request: Request, body: ApproveBody):
    require_admin(request)
    wanted = normalised_code(body.code)
    await ask_brain("POST", f"{API_SUFFIX}/device/approve", json_body={"user_code": wanted, "approve": body.approve})
    return {"ok": True}


@router.get("")
async def list_devices(request: Request):
    require_admin(request)
    answer = await ask_brain("GET", f"{API_SUFFIX}/devices")
    return {"devices": answer.get("devices", [])}


@router.delete("/{device_id}")
async def turn_off(request: Request, device_id: int):
    require_admin(request)
    await ask_brain("DELETE", f"{API_SUFFIX}/devices/{device_id}")
    return {"ok": True}


@router.post("/keys")
async def make_key(request: Request, body: KeyBody, response: Response):
    require_admin(request)
    made = await ask_brain("POST", f"{API_SUFFIX}/devices", json_body={"name": body.name.strip()})
    key = made.get("api_key")
    if not isinstance(key, str) or not key:
        raise HTTPException(502, "Arty didn't send back a key.")
    response.headers["Cache-Control"] = "no-store"
    return {"id": made.get("id"), "name": made.get("name", body.name), "api_key": key, "setup_code": phone_setup_code(key)}
