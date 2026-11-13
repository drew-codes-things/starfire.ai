import logging

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from auth import (ACCOUNT_LOCKOUT_SECONDS, ACCOUNT_MAX_FAILED_LOGINS, MAX_FIELD_CHARS, SESSION_COOKIE,
                  SESSION_LIFETIME_SECONDS, AccountError, KnownAddresses, LoginLimiter, SessionStore, UserStore,
                  client_address, current_user, normalise_email, parse_networks, require_admin)
from config import config

router = APIRouter(prefix="/api/auth")
users = UserStore(config.data_dir)
sessions = SessionStore(config.data_dir)
limiter = LoginLimiter()
account_limiter = LoginLimiter(max_failures=ACCOUNT_MAX_FAILED_LOGINS, lockout_seconds=ACCOUNT_LOCKOUT_SECONDS)
known_addresses = KnownAddresses(config.data_dir)
logger = logging.getLogger(__name__)
TRUSTED_PROXIES = parse_networks(config.trusted_proxies)

MAX_FIELD_LENGTH = MAX_FIELD_CHARS


class LoginRequest(BaseModel):
    email: str
    password: str


class SetupRequest(BaseModel):
    email: str
    name: str = ""
    password: str
    confirm: str


class PasswordRequest(BaseModel):
    current: str
    password: str
    confirm: str


class NewUserRequest(BaseModel):
    email: str
    name: str = ""
    password: str
    role: str = "user"


class UpdateUserRequest(BaseModel):
    name: str | None = None
    role: str | None = None
    disabled: bool | None = None


class ResetPasswordRequest(BaseModel):
    password: str


def _admin(request: Request):
    try:
        return require_admin(request)
    except AccountError as error:
        raise HTTPException(status_code=403 if "admin" in str(error) else 401, detail=str(error))


def _registry():
    import user_data

    return user_data.registry


def _start_session(response: Response, user_id: str) -> None:
    response.set_cookie(SESSION_COOKIE, sessions.create(user_id), max_age=SESSION_LIFETIME_SECONDS,
                        httponly=True, secure=config.cookie_secure, samesite="lax", path="/")


def _already_signed_in_as(request: Request, email: str) -> bool:
    user = current_user(request, users, sessions)
    return user is not None and user.email == email


def _account_wait(request: Request, email: str, address: str) -> int:
    if known_addresses.is_known(email, address) or _already_signed_in_as(request, email):
        return 0
    return account_limiter.seconds_locked(email)


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response):
    if len(body.email) > MAX_FIELD_LENGTH or len(body.password) > MAX_FIELD_LENGTH:
        raise HTTPException(status_code=400, detail="email or password is too long")
    address = client_address(request, TRUSTED_PROXIES, config.client_ip_header)
    email = normalise_email(body.email)
    limit_key = f"{address}|{email}"
    wait = limiter.seconds_locked(limit_key) or _account_wait(request, email, address)
    if wait:
        raise HTTPException(status_code=429, detail=f"too many attempts - try again in {wait // 60 + 1} min")
    user = users.verify(body.email, body.password)
    if not user:
        limiter.record_failure(limit_key)
        account_limiter.record_failure(email)
        logger.warning("failed sign-in for %s from %s", email, address)
        raise HTTPException(status_code=401, detail="wrong email or password")
    limiter.reset(limit_key)
    known_addresses.remember(email, address)
    _start_session(response, user.id)
    return user.public()


@router.get("/status")
def status():
    return {"has_users": users.has_users()}


@router.post("/setup")
def setup(body: SetupRequest, response: Response):
    try:
        user = users.create_first(body.email, body.name, body.password, body.confirm)
    except AccountError as error:
        raise HTTPException(status_code=400, detail=str(error))
    registry = _registry()
    if registry is not None:
        registry.migrate_legacy(user.id)
    _start_session(response, user.id)
    return user.public()


@router.get("/users")
def list_users(request: Request):
    _admin(request)
    return {"users": [u.public() for u in users.list()]}


@router.post("/users")
def create_user(body: NewUserRequest, request: Request):
    _admin(request)
    if len(body.email) > MAX_FIELD_LENGTH or len(body.password) > MAX_FIELD_LENGTH:
        raise HTTPException(status_code=400, detail="email or password is too long")
    try:
        user = users.create(body.email, body.name, body.password, body.role)
    except AccountError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return user.public()


@router.patch("/users/{user_id}")
def update_user(user_id: str, body: UpdateUserRequest, request: Request):
    admin = _admin(request)
    if user_id == admin.id and (body.role not in (None, "admin") or body.disabled):
        raise HTTPException(status_code=400, detail="you can't demote or disable your own account")
    try:
        user = users.update(user_id, name=body.name, role=body.role, disabled=body.disabled)
    except AccountError as error:
        raise HTTPException(status_code=404 if "no such" in str(error) else 400, detail=str(error))
    if user.disabled:
        sessions.revoke_all_for(user.id)
    return user.public()


@router.post("/users/{user_id}/password")
def reset_user_password(user_id: str, body: ResetPasswordRequest, request: Request):
    _admin(request)
    if len(body.password) > MAX_FIELD_LENGTH:
        raise HTTPException(status_code=400, detail="that password is too long")
    try:
        users.set_password(user_id, body.password, body.password)
    except AccountError as error:
        raise HTTPException(status_code=404 if "no such" in str(error) else 400, detail=str(error))
    sessions.revoke_all_for(user_id)
    return {"ok": True}


@router.delete("/users/{user_id}")
def delete_user(user_id: str, request: Request):
    admin = _admin(request)
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="you can't delete your own account")
    try:
        removed = users.delete(user_id)
    except AccountError as error:
        raise HTTPException(status_code=400, detail=str(error))
    if not removed:
        raise HTTPException(status_code=404, detail="no such account")
    sessions.revoke_all_for(user_id)
    registry = _registry()
    if registry is not None:
        registry.delete_user_data(user_id)
    return {"ok": True}


@router.post("/password")
def change_password(body: PasswordRequest, request: Request, response: Response):
    user = current_user(request, users, sessions)
    if not user:
        raise HTTPException(status_code=401, detail="not logged in")
    try:
        users.change_password(user.id, body.current, body.password, body.confirm)
    except AccountError as error:
        raise HTTPException(status_code=400, detail=str(error))
    sessions.revoke_all_for(user.id)
    _start_session(response, user.id)
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE, "")
    if token:
        sessions.revoke(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    user = current_user(request, users, sessions)
    if not user:
        raise HTTPException(status_code=401, detail="not logged in")
    return user.public()
