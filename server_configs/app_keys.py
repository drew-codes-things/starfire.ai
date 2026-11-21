from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from atomic_io import atomic_write_json, holds_file_lock, report_unreadable_store

KEYS_FILE = "app_keys.json"
KEY_PREFIX = "sf_"
KEY_RANDOM_BYTES = 32
KEY_SHAPE = re.compile(r"^sf_[A-Za-z0-9_-]{43,64}$")
MAX_KEY_NAME_CHARS = 80
MAX_KEY_MODELS = 100
MAX_MODEL_NAME_CHARS = 200
MAX_DAILY_REPLIES = 1_000_000
MAX_EXPIRY_DAYS = 3650
DAY_SECONDS = 24 * 3600
PRIVATE_FILE_MODE = 0o600


class AppKeyError(ValueError):
    pass


@dataclass
class AppKey:
    id: str
    name: str
    user_id: str
    digest: str
    models: list[str] | None = None
    daily_limit: int | None = None
    expires: float | None = None
    created: float = 0.0
    last_used: float | None = None
    used_day: str = ""
    used_today: int = 0
    shown_start: str = ""

    def is_expired(self, now: float) -> bool:
        return self.expires is not None and now >= self.expires

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "user_id": self.user_id, "models": self.models,
                "daily_limit": self.daily_limit, "expires": self.expires, "created": self.created,
                "last_used": self.last_used, "shown_start": self.shown_start}


@dataclass(frozen=True)
class DailyLimitReached:
    limit: int
    retry_after_seconds: int


def key_digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def make_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(KEY_RANDOM_BYTES)


def looks_like_key(text: str) -> bool:
    return isinstance(text, str) and bool(KEY_SHAPE.fullmatch(text))


def utc_day(moment: float) -> str:
    return datetime.fromtimestamp(moment, timezone.utc).date().isoformat()


def seconds_to_next_utc_day(moment: float) -> int:
    day_start = datetime.fromisoformat(utc_day(moment)).replace(tzinfo=timezone.utc).timestamp()
    return max(1, int(day_start + DAY_SECONDS - moment))


def cleaned_name(name: str) -> str:
    kept = (name or "").strip()
    if not kept or len(kept) > MAX_KEY_NAME_CHARS:
        raise AppKeyError(f"give the key a name of 1 to {MAX_KEY_NAME_CHARS} characters")
    return kept


def cleaned_models(models) -> list[str] | None:
    if models is None:
        return None
    if not isinstance(models, list) or not all(isinstance(model, str) for model in models):
        raise AppKeyError("models must be a list of model names, or null for every model the account may use")
    kept = list(dict.fromkeys(model.strip() for model in models if model.strip()))
    if not kept:
        raise AppKeyError("pick at least one model, or allow every model the account may use")
    if len(kept) > MAX_KEY_MODELS or any(len(model) > MAX_MODEL_NAME_CHARS for model in kept):
        raise AppKeyError(f"at most {MAX_KEY_MODELS} models of up to {MAX_MODEL_NAME_CHARS} characters each")
    return kept


def cleaned_daily_limit(limit) -> int | None:
    if limit is None:
        return None
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_DAILY_REPLIES:
        raise AppKeyError(f"the daily reply limit must be a whole number from 1 to {MAX_DAILY_REPLIES}, or empty")
    return limit


def expiry_after(days, now: float) -> float | None:
    if days is None:
        return None
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_EXPIRY_DAYS:
        raise AppKeyError(f"expiry must be a whole number of days from 1 to {MAX_EXPIRY_DAYS}, or empty")
    return now + days * DAY_SECONDS


def names_the_model(listed_name: str, wanted: str) -> bool:
    return listed_name.strip().lower() == wanted.strip().lower()


def key_allows_model(key: AppKey, model: str) -> bool:
    return key.models is None or any(names_the_model(allowed, model) for allowed in key.models)


@dataclass
class AppKeyStore:
    data_dir: str
    clock: object = field(default=time.time)

    @property
    def path(self) -> str:
        return os.path.join(self.data_dir, KEYS_FILE)

    def _load(self) -> list[AppKey]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as error:
            report_unreadable_store(self.path, error)
            return []
        if not isinstance(raw, list):
            return []
        keys = []
        for item in raw:
            try:
                keys.append(AppKey(**item))
            except TypeError:
                continue
        return keys

    def _save(self, keys: list[AppKey]) -> None:
        atomic_write_json(self.path, [asdict(key) for key in keys])
        os.chmod(self.path, PRIVATE_FILE_MODE)

    def list(self) -> list[AppKey]:
        return self._load()

    @holds_file_lock()
    def create(self, name: str, user_id: str, models=None, daily_limit=None, expires_in_days=None) -> tuple[AppKey, str]:
        now = self.clock()
        secret = make_key()
        key = AppKey(id=uuid.uuid4().hex[:12], name=cleaned_name(name), user_id=user_id, digest=key_digest(secret),
                     models=cleaned_models(models), daily_limit=cleaned_daily_limit(daily_limit),
                     expires=expiry_after(expires_in_days, now), created=now,
                     shown_start=secret[:len(KEY_PREFIX) + 4])
        keys = self._load()
        keys.append(key)
        self._save(keys)
        return key, secret

    @holds_file_lock()
    def revoke(self, key_id: str) -> AppKey | None:
        keys = self._load()
        gone = next((key for key in keys if key.id == key_id), None)
        if gone is None:
            return None
        self._save([key for key in keys if key.id != key_id])
        return gone

    @holds_file_lock()
    def revoke_all_for(self, user_id: str) -> int:
        keys = self._load()
        kept = [key for key in keys if key.user_id != user_id]
        if len(kept) != len(keys):
            self._save(kept)
        return len(keys) - len(kept)

    def find(self, secret: str) -> AppKey | None:
        if not looks_like_key(secret):
            return None
        wanted = key_digest(secret)
        now = self.clock()
        found = next((key for key in self._load() if secrets.compare_digest(key.digest, wanted)), None)
        if found is None or found.is_expired(now):
            return None
        return found

    def is_live(self, key_id: str) -> bool:
        now = self.clock()
        return any(key.id == key_id and not key.is_expired(now) for key in self._load())

    @holds_file_lock()
    def take_reply(self, key_id: str) -> DailyLimitReached | None:
        now = self.clock()
        keys = self._load()
        key = next((key for key in keys if key.id == key_id), None)
        if key is None or key.is_expired(now):
            raise AppKeyError("that app key was revoked or has expired")
        today = utc_day(now)
        used = key.used_today if key.used_day == today else 0
        if key.daily_limit is not None and used >= key.daily_limit:
            return DailyLimitReached(key.daily_limit, seconds_to_next_utc_day(now))
        key.used_day, key.used_today, key.last_used = today, used + 1, now
        self._save(keys)
        return None

    @holds_file_lock()
    def give_back_reply(self, key_id: str) -> None:
        keys = self._load()
        key = next((key for key in keys if key.id == key_id), None)
        if key is None or key.used_today <= 0:
            return
        key.used_today -= 1
        self._save(keys)
