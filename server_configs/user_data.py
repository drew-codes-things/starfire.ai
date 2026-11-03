from __future__ import annotations

import contextvars
import os
import re
import shutil
from contextlib import contextmanager
from types import SimpleNamespace

from attachments import AttachmentStore
from chat_session_store import ChatSessionStore
from documents_store import DocumentStore
from email_store import EmailAccountStore, EmailRuleStore
from folder_store import FolderStore
from local_gen_store import GeneratedFileStore
from memory_store import MemoryStore
from note_store import DEFAULT_TEMPLATES, NoteStore, NoteTemplateStore
from preset_store import PresetStore
from prompt_store import PromptStore
from task_store import TaskRunStore, TaskStore
from usage_store import UsageStore

USERS_SUBDIR = "users"
PER_USER_FILES = (
    "chat_sessions.json", "chat_folders.json", "memory.json", "documents.json", "notes.json",
    "note_templates.json", "tasks.json", "task_runs.json", "email_accounts.json", "email_rules.json",
    "presets.json", "prompts.json", "usage.json", "generated_files.json", "openwebui_imported.json",
)
PER_USER_DIRS = ("generated", "attachments")
USER_ID_RE = re.compile(r"^[0-9a-f]{32}$")

current_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("starfire_user_id", default=None)


class NoUserContext(RuntimeError):
    pass


def build_stores(data_dir: str) -> SimpleNamespace:
    return SimpleNamespace(
        chat_sessions=ChatSessionStore(data_dir),
        chat_folders=FolderStore(data_dir),
        memory=MemoryStore(data_dir),
        documents=DocumentStore(data_dir),
        notes=NoteStore(data_dir),
        note_templates=NoteTemplateStore(data_dir),
        tasks=TaskStore(data_dir),
        task_runs=TaskRunStore(data_dir),
        email_accounts=EmailAccountStore(data_dir),
        email_rules=EmailRuleStore(data_dir),
        presets=PresetStore(data_dir),
        prompts=PromptStore(data_dir),
        usage=UsageStore(data_dir),
        generated_files=GeneratedFileStore(data_dir),
        attachment_store=AttachmentStore(data_dir),
    )


class UserDataRegistry:

    def __init__(self, data_dir: str):
        self.data_dir = os.path.abspath(data_dir)
        self.users_dir = os.path.join(self.data_dir, USERS_SUBDIR)
        self._cache: dict[str, SimpleNamespace] = {}

    def dir_for(self, user_id: str) -> str:
        if not USER_ID_RE.match(user_id or ""):
            raise NoUserContext(f"not a user id: {user_id!r}")
        return os.path.join(self.users_dir, user_id)

    def for_user(self, user_id: str) -> SimpleNamespace:
        stores = self._cache.get(user_id)
        if stores is None:
            directory = self.dir_for(user_id)
            os.makedirs(directory, exist_ok=True)
            stores = build_stores(directory)
            if not stores.note_templates.list():
                for template in DEFAULT_TEMPLATES:
                    stores.note_templates.add(**template)
            self._cache[user_id] = stores
        return stores

    def user_ids(self) -> list[str]:
        if not os.path.isdir(self.users_dir):
            return []
        return sorted(name for name in os.listdir(self.users_dir)
                      if USER_ID_RE.match(name) and os.path.isdir(os.path.join(self.users_dir, name)))

    def forget(self, user_id: str) -> None:
        self._cache.pop(user_id, None)

    def delete_user_data(self, user_id: str) -> bool:
        directory = self.dir_for(user_id)
        self.forget(user_id)
        if not os.path.isdir(directory):
            return False
        shutil.rmtree(directory)
        return True

    def legacy_items(self) -> list[str]:
        return [name for name in PER_USER_FILES + PER_USER_DIRS if os.path.exists(os.path.join(self.data_dir, name))]

    def migrate_legacy(self, user_id: str) -> list[str]:
        moved: list[str] = []
        items = self.legacy_items()
        if not items:
            return moved
        directory = self.dir_for(user_id)
        os.makedirs(directory, exist_ok=True)
        for name in items:
            source = os.path.join(self.data_dir, name)
            target = os.path.join(directory, name)
            if os.path.exists(target):
                if os.path.isdir(source) and not os.listdir(source):
                    os.rmdir(source)
                continue
            shutil.move(source, target)
            moved.append(name)
        self.forget(user_id)
        return moved


registry: UserDataRegistry | None = None


def configure(data_dir: str) -> UserDataRegistry:
    global registry
    registry = UserDataRegistry(data_dir)
    return registry


def stores_for_current_user() -> SimpleNamespace:
    user_id = current_user_id.get()
    if not user_id:
        raise NoUserContext("no signed-in user for this operation")
    if registry is None:
        raise NoUserContext("user data storage has not been configured")
    return registry.for_user(user_id)


class PerUser:

    def __init__(self, attribute: str):
        self._attribute = attribute

    def __getattr__(self, name: str):
        return getattr(getattr(stores_for_current_user(), self._attribute), name)

    def __repr__(self) -> str:
        return f"PerUser({self._attribute})"


@contextmanager
def run_as(user_id: str | None):
    token = current_user_id.set(user_id)
    try:
        yield
    finally:
        current_user_id.reset(token)
