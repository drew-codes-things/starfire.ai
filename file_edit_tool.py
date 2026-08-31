from __future__ import annotations

import difflib
import os
import uuid

def read_current(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
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

def stage(path: str, content: str, diff_text: str) -> str:
    edit_id = uuid.uuid4().hex[:12]
    _pending[edit_id] = {"id": edit_id, "path": path, "content": content, "diff": diff_text}
    return edit_id

def get_pending(edit_id: str) -> dict | None:
    return _pending.get(edit_id)

def remove_pending(edit_id: str) -> bool:
    return _pending.pop(edit_id, None) is not None

def list_pending() -> list[dict]:
    return list(_pending.values())
