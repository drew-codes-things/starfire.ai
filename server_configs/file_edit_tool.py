from __future__ import annotations

import difflib
import logging
import os
import uuid

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

def apply_edit(path: str, new_content: str) -> dict:
    try:
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "error": str(e)}

_pending: dict[str, dict] = {}

def stage(path: str, content: str, diff_text: str, owner: str | None) -> str:
    edit_id = uuid.uuid4().hex[:12]
    _pending[edit_id] = {"id": edit_id, "owner": owner, "path": path, "content": content, "diff": diff_text}
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
