from __future__ import annotations

from auth import User, UserStore
from model_access import ModelAccessStore


def with_default_models(users: UserStore, data_dir: str, user: User) -> User:
    try:
        ModelAccessStore(data_dir).apply_default_to(user.id)
    except Exception:
        users.discard(user.id)
        raise
    return user


def create_account(users: UserStore, data_dir: str, email: str, name: str, password: str, role: str) -> User:
    return with_default_models(users, data_dir, users.create(email, name, password, role))


def import_account(users: UserStore, data_dir: str, email: str, name: str, role: str, password_hash: str) -> User:
    is_new = users.find_by_email(email) is None
    user = users.upsert(email=email, name=name, role=role, password_hash=password_hash)
    return with_default_models(users, data_dir, user) if is_new else user
