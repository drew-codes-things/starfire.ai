from __future__ import annotations

import difflib
import logging
import os
import uuid

from host_sandbox import HostLimits

logger = logging.getLogger(__name__)

def read_current(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.debug("%s does not exist yet, so an edit starts from nothing", path)
        return ""
    except (OSError, UnicodeDecodeError) as error:
        logger.warning("could not read %s as text, so an edit diff shows it as empty: %s", path, error)
        return ""

def make_diff(path: str, old_content: str, new_content: str) -> str:
    diff = difflib.unified_diff(
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=path, tofile=path,
    )
    text = "".join(diff)
    return text or "(no changes - new content is identical to the current file)"

class EditRefused(ValueError):
    pass


OUTSIDE_THE_ALLOWED_FOLDERS = ("edit_file can only change files in this account's starfire workspace folder or in a "
                               "folder an admin allowed; use a path relative to the workspace")


def _is_within(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def resolve_target(path: str, limits: HostLimits) -> str:
    if not limits.workspace:
        raise EditRefused(OUTSIDE_THE_ALLOWED_FOLDERS)
    workspace = os.path.realpath(limits.workspace)
    target = os.path.realpath(os.path.join(workspace, os.path.expanduser(path)))
    if _is_within(target, workspace) and target != workspace:
        return target
    if any(_is_within(target, os.path.realpath(folder)) for folder in limits.protected):
        raise EditRefused(OUTSIDE_THE_ALLOWED_FOLDERS)
    if any(_is_within(target, os.path.realpath(root)) for root in limits.edit_roots):
        return target
    raise EditRefused(OUTSIDE_THE_ALLOWED_FOLDERS)


def apply_edit(path: str, new_content: str, limits: HostLimits) -> dict:
    try:
        target = resolve_target(path, limits)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
        with os.fdopen(descriptor, "w", encoding="utf-8") as f:
            f.write(new_content)
        return {"ok": True}
    except (EditRefused, OSError) as e:
        return {"ok": False, "error": str(e)}

_pending: dict[str, dict] = {}

def stage(path: str, content: str, diff_text: str, owner: str | None, untrusted: bool = False) -> str:
    edit_id = uuid.uuid4().hex[:12]
    _pending[edit_id] = {"id": edit_id, "owner": owner, "path": path, "content": content, "diff": diff_text,
                         "untrusted": untrusted}
    return edit_id

def get_pending(edit_id: str, owner: str | None) -> dict | None:
    edit = _pending.get(edit_id)
    return edit if edit and edit["owner"] == owner else None

def remove_pending(edit_id: str, owner: str | None) -> bool:
    if not get_pending(edit_id, owner):
        return False
    del _pending[edit_id]
    return True

def list_pending(owner: str | None) -> list[dict]:
    return [edit for edit in _pending.values() if edit["owner"] == owner]
