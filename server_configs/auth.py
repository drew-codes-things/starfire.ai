from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import secrets
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import bcrypt
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

import user_data
from atomic_io import atomic_write_json

SESSION_COOKIE = "starfire_session"
SESSION_LIFETIME_SECONDS = 30 * 24 * 3600
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 15 * 60
LOGIN_PAGE = "/login.html"
SETUP_PAGE = "/setup.html"
PUBLIC_PATHS = frozenset({LOGIN_PAGE, "/login.js", "/style.css", "/api/auth/login", "/api/auth/status", "/api/health"})
SETUP_PATHS = frozenset({SETUP_PAGE, "/setup.js", "/api/auth/setup"})
MIN_PASSWORD_CHARS = 10
MAX_FIELD_CHARS = 256
PUBLIC_PREFIXES = ("/assets/",)
ROLES = ("admin", "user")
UNUSED_HASH = bcrypt.hashpw(b"starfire-timing-equaliser", bcrypt.gensalt()).decode()


class AccountError(ValueError):
    pass


def check_new_password(password: str, confirm: str) -> None:
    if len(password) < MIN_PASSWORD_CHARS:
        raise AccountError(f"use at least {MIN_PASSWORD_CHARS} characters")
    if len(password) > MAX_FIELD_CHARS:
        raise AccountError("that password is too long")
    if password != confirm:
        raise AccountError("the two passwords don't match")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


ADMIN_ONLY_PREFIXES = ("/api/model-endpoints", "/api/mcp/servers", "/api/comfyui", "/api/piper",
                       "/api/custom-workflows", "/api/hardware", "/api/restore", "/api/auth/users")
ADMIN_ONLY_PATHS = frozenset({"/api/backup"})
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_admin_only(method: str, path: str) -> bool:
    if path in ADMIN_ONLY_PATHS or path.startswith("/api/auth/users"):
        return True
    return method not in READ_METHODS and path.startswith(ADMIN_ONLY_PREFIXES)


@dataclass
class User:
    id: str
    email: str
    name: str
    role: str
    password_hash: str
    disabled: bool = False
    created: str = ""

    def public(self) -> dict:
        return {"id": self.id, "email": self.email, "name": self.name, "role": self.role,
                "disabled": self.disabled, "created": self.created}


def _read_json(path: str, expected_type: type):
    if not os.path.exists(path):
        return expected_type()
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, expected_type):
        raise ValueError(f"{path} is not a JSON {expected_type.__name__}")
    return raw


def normalise_email(email: str) -> str:
    return email.strip().lower()


class UserStore:

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "users.json")
        self._lock = threading.Lock()

    def list(self) -> list[User]:
        return [User(**item) for item in _read_json(self.path, list)]

    def has_users(self) -> bool:
        return bool(self.list())

    def find_by_email(self, email: str) -> User | None:
        wanted = normalise_email(email)
        return next((u for u in self.list() if u.email == wanted), None)

    def find_by_id(self, user_id: str) -> User | None:
        return next((u for u in self.list() if u.id == user_id), None)

    def verify(self, email: str, password: str) -> User | None:
        user = self.find_by_email(email)
        stored_hash = user.password_hash if user else UNUSED_HASH
        matches = bcrypt.checkpw(password.encode(), stored_hash.encode())
        return user if user and matches and not user.disabled else None

    def first_admin(self) -> User | None:
        admins = [u for u in self.list() if u.role == "admin"]
        return admins[0] if admins else None

    def admin_count(self) -> int:
        return sum(1 for u in self.list() if u.role == "admin" and not u.disabled)

    def _write(self, users: list[User]) -> None:
        atomic_write_json(self.path, [asdict(u) for u in users])
        os.chmod(self.path, 0o600)

    def create(self, email: str, name: str, password: str, role: str = "user") -> User:
        email = normalise_email(email)
        if "@" not in email or "." not in email.split("@")[-1] or len(email) > MAX_FIELD_CHARS:
            raise AccountError("enter a valid email address")
        if len(name) > MAX_FIELD_CHARS:
            raise AccountError("that name is too long")
        if role not in ROLES:
            raise AccountError(f"role must be one of {', '.join(ROLES)}")
        check_new_password(password, password)
        with self._lock:
            users = self.list()
            if any(u.email == email for u in users):
                raise AccountError("an account with that email already exists")
            user = User(id=uuid.uuid4().hex, email=email, name=name.strip() or email, role=role,
                        password_hash=hash_password(password), created=_now_iso())
            users.append(user)
            self._write(users)
        return user

    def update(self, user_id: str, name: str | None = None, role: str | None = None,
               disabled: bool | None = None) -> User:
        if role is not None and role not in ROLES:
            raise AccountError(f"role must be one of {', '.join(ROLES)}")
        if name is not None and len(name) > MAX_FIELD_CHARS:
            raise AccountError("that name is too long")
        with self._lock:
            users = self.list()
            target = next((u for u in users if u.id == user_id), None)
            if not target:
                raise AccountError("no such account")
            if name is not None:
                target.name = name.strip() or target.email
            if role is not None:
                target.role = role
            if disabled is not None:
                target.disabled = disabled
            others_admin = any(u.role == "admin" and not u.disabled for u in users if u.id != user_id)
            if (target.role != "admin" or target.disabled) and not others_admin:
                raise AccountError("there must always be at least one active admin")
            self._write(users)
        return target

    def delete(self, user_id: str) -> bool:
        with self._lock:
            users = self.list()
            target = next((u for u in users if u.id == user_id), None)
            if not target:
                return False
            remaining = [u for u in users if u.id != user_id]
            if not any(u.role == "admin" and not u.disabled for u in remaining):
                raise AccountError("there must always be at least one active admin")
            self._write(remaining)
        return True

    def create_first(self, email: str, name: str, password: str, confirm: str) -> User:
        email = normalise_email(email)
        if "@" not in email or "." not in email.split("@")[-1] or len(email) > MAX_FIELD_CHARS:
            raise AccountError("enter a valid email address")
        if len(name) > MAX_FIELD_CHARS:
            raise AccountError("that name is too long")
        check_new_password(password, confirm)
        with self._lock:
            if self.list():
                raise AccountError("an account already exists - log in instead")
            user = User(id=uuid.uuid4().hex, email=email, name=name.strip() or email, role="admin",
                        password_hash=hash_password(password), created=_now_iso())
            atomic_write_json(self.path, [asdict(user)])
            os.chmod(self.path, 0o600)
        return user

    def change_password(self, user_id: str, current: str, password: str, confirm: str) -> None:
        user = self.find_by_id(user_id)
        if not user or not self.verify(user.email, current[:MAX_FIELD_CHARS]):
            raise AccountError("your current password is wrong")
        check_new_password(password, confirm)
        with self._lock:
            users = self.list()
            for stored in users:
                if stored.id == user_id:
                    stored.password_hash = hash_password(password)
            atomic_write_json(self.path, [asdict(u) for u in users])
            os.chmod(self.path, 0o600)

    def set_password(self, user_id: str, password: str, confirm: str) -> None:
        check_new_password(password, confirm)
        with self._lock:
            users = self.list()
            if not any(u.id == user_id for u in users):
                raise AccountError("no such account")
            for stored in users:
                if stored.id == user_id:
                    stored.password_hash = hash_password(password)
            atomic_write_json(self.path, [asdict(u) for u in users])
            os.chmod(self.path, 0o600)

    def upsert(self, email: str, name: str, role: str, password_hash: str) -> User:
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}, not {role!r}")
        if not password_hash.startswith(("$2a$", "$2b$", "$2y$")):
            raise ValueError("password hash is not a bcrypt hash")
        email = normalise_email(email)
        if "@" not in email:
            raise ValueError(f"{email!r} is not an email address")
        with self._lock:
            users = self.list()
            existing = next((u for u in users if u.email == email), None)
            user = User(id=existing.id if existing else uuid.uuid4().hex, email=email,
                        name=name.strip() or email, role=role, password_hash=password_hash)
            users = [u for u in users if u.email != email] + [user]
            atomic_write_json(self.path, [asdict(u) for u in users])
            os.chmod(self.path, 0o600)
        return user


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SessionStore:

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "sessions.json")
        self._lock = threading.Lock()

    def _live(self, sessions: dict, now: float) -> dict:
        return {digest: s for digest, s in sessions.items() if s.get("expires", 0) > now}

    def create(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            sessions = self._live(_read_json(self.path, dict), now)
            sessions[_token_digest(token)] = {"user_id": user_id, "expires": now + SESSION_LIFETIME_SECONDS}
            atomic_write_json(self.path, sessions)
            os.chmod(self.path, 0o600)
        return token

    def user_id_for(self, token: str) -> str | None:
        if not token:
            return None
        session = _read_json(self.path, dict).get(_token_digest(token))
        if not session or session.get("expires", 0) <= time.time():
            return None
        return session.get("user_id")

    def revoke(self, token: str) -> None:
        with self._lock:
            sessions = _read_json(self.path, dict)
            if sessions.pop(_token_digest(token), None) is not None:
                atomic_write_json(self.path, sessions)

    def revoke_all_for(self, user_id: str) -> None:
        with self._lock:
            sessions = _read_json(self.path, dict)
            atomic_write_json(self.path, {d: s for d, s in sessions.items() if s.get("user_id") != user_id})
            os.chmod(self.path, 0o600)


class LoginLimiter:

    def __init__(self, max_failures: int = MAX_FAILED_LOGINS, lockout_seconds: int = LOCKOUT_SECONDS):
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        return [t for t in self._failures.get(key, []) if now - t < self.lockout_seconds]

    def seconds_locked(self, key: str) -> int:
        now = time.time()
        with self._lock:
            recent = self._recent(key, now)
            if len(recent) < self.max_failures:
                return 0
            return int(self.lockout_seconds - (now - recent[0])) + 1

    def record_failure(self, key: str) -> None:
        now = time.time()
        with self._lock:
            self._failures[key] = self._recent(key, now) + [now]

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def client_address(request: Request) -> str:
    peer = request.client.host if request.client else ""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded and is_loopback(peer):
        return forwarded.split(",")[0].strip()
    return peer


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def current_user(request: Request, users: UserStore, sessions: SessionStore) -> User | None:
    user_id = sessions.user_id_for(request.cookies.get(SESSION_COOKIE, ""))
    user = users.find_by_id(user_id) if user_id else None
    return user if user and not user.disabled else None


def require_admin(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if not user:
        raise AccountError("not logged in")
    if user.role != "admin":
        raise AccountError("admin only")
    return user


def install(app: FastAPI, users: UserStore, sessions: SessionStore) -> None:

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        if not users.has_users():
            if path in SETUP_PATHS or (is_public(path) and path != LOGIN_PAGE):
                return await call_next(request)
            if path.startswith("/api/"):
                return JSONResponse({"detail": "set up an account first", "setup": True}, status_code=401)
            return RedirectResponse(SETUP_PAGE, status_code=303)
        if path in SETUP_PATHS:
            if path.startswith("/api/"):
                return JSONResponse({"detail": "an account already exists - log in instead"}, status_code=409)
            return RedirectResponse(LOGIN_PAGE, status_code=303)
        if is_public(path):
            return await call_next(request)
        user = current_user(request, users, sessions)
        if user:
            if user.role != "admin" and is_admin_only(request.method, path):
                return JSONResponse({"detail": "only an admin can do that"}, status_code=403)
            request.state.user = user
            token = user_data.current_user_id.set(user.id)
            try:
                return await call_next(request)
            finally:
                user_data.current_user_id.reset(token)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "not logged in"}, status_code=401)
        return RedirectResponse(LOGIN_PAGE, status_code=303)
