"""File editing with a diff preview — the `edit_file` builtin tool. Writes
the file's *entire new content* (simplest contract that works uniformly for
both new and existing files, no old-text/new-text matching to get wrong)
and always computes a unified diff against whatever was there before, so the
change is visible in chat either way — this isn't a silent overwrite even in
auto-apply mode.

Two modes, chosen per-request by the frontend's "require approval" toggle
(see ChatBody.require_edit_approval in routes.py), not a global setting:
  - auto (default): apply immediately, return the diff.
  - approval: stage the edit (pending_edits_store) and return the diff
    without writing anything — the user approves or rejects it from the
    chat UI, which is what actually calls apply_edit().
"""

from __future__ import annotations

import difflib
import os


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
    return text or "(no changes — new content is identical to the current file)"


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
