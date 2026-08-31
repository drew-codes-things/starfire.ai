"""In-memory queue of staged file edits awaiting user approval (approval
mode of the edit_file tool — see file_edit_tool.py). Deliberately NOT
persisted to disk: a pending edit only makes sense within the browser tab
that's still looking at the conversation that proposed it, and losing
unapproved edits on a server restart is the safe failure mode, not a
data-loss one (nothing was written to disk yet).
"""

from __future__ import annotations

import uuid

_pending: dict[str, dict] = {}


def stage(path: str, content: str, diff_text: str) -> str:
    edit_id = uuid.uuid4().hex[:12]
    _pending[edit_id] = {"id": edit_id, "path": path, "content": content, "diff": diff_text}
    return edit_id


def get(edit_id: str) -> dict | None:
    return _pending.get(edit_id)


def remove(edit_id: str) -> bool:
    return _pending.pop(edit_id, None) is not None


def list_pending() -> list[dict]:
    return list(_pending.values())
