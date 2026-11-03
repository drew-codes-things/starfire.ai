from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from auth import (MAX_FIELD_CHARS, SESSION_COOKIE, SESSION_LIFETIME_SECONDS, AccountError, LoginLimiter, SessionStore,
                  UserStore, client_address, current_user, normalise_email, require_admin)
from config import config

router = APIRouter(prefix="/api/auth")
users = UserStore(config.data_dir)
sessions = SessionStore(config.data_dir)
limiter = LoginLimiter()

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


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response):
    if len(body.email) > MAX_FIELD_LENGTH or len(body.password) > MAX_FIELD_LENGTH:
        raise HTTPException(status_code=400, detail="email or password is too long")
    limit_key = f"{client_address(request)}|{normalise_email(body.email)}"
    wait = limiter.seconds_locked(limit_key)
    if wait:
        raise HTTPException(status_code=429, detail=f"too many attempts - try again in {wait // 60 + 1} min")
    user = users.verify(body.email, body.password)
    if not user:
        limiter.record_failure(limit_key)
        raise HTTPException(status_code=401, detail="wrong email or password")
    limiter.reset(limit_key)
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
