from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from auth import (SESSION_COOKIE, SESSION_LIFETIME_SECONDS, LoginLimiter, SessionStore, UserStore,
                  client_address, current_user, normalise_email)
from config import config

router = APIRouter(prefix="/api/auth")
users = UserStore(config.data_dir)
sessions = SessionStore(config.data_dir)
limiter = LoginLimiter()

MAX_FIELD_LENGTH = 256


class LoginRequest(BaseModel):
    email: str
    password: str


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
    response.set_cookie(SESSION_COOKIE, sessions.create(user.id), max_age=SESSION_LIFETIME_SECONDS,
                        httponly=True, secure=config.cookie_secure, samesite="lax", path="/")
    return user.public()


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
