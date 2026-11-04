from __future__ import annotations

import asyncio
import time
import uuid

TIMEOUT_SECONDS = 120
MAX_OUTPUT_CHARS = 8000
APPROVAL_TIMEOUT_SECONDS = 300

_pending: dict[str, dict] = {}


def stage(command: str, cwd: str | None, owner: str | None) -> str:
    pending_id = uuid.uuid4().hex[:12]
    _pending[pending_id] = {
        "id": pending_id, "owner": owner, "command": command, "cwd": cwd or "",
        "created": time.time(), "future": asyncio.get_running_loop().create_future(),
    }
    return pending_id


async def wait_for_decision(pending_id: str) -> bool | None:
    entry = _pending.get(pending_id)
    if not entry:
        return None
    try:
        return await asyncio.wait_for(asyncio.shield(entry["future"]), timeout=APPROVAL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return None
    finally:
        _pending.pop(pending_id, None)


def resolve(pending_id: str, approved: bool, owner: str | None) -> bool:
    entry = _pending.get(pending_id)
    if not entry or entry["owner"] != owner or entry["future"].done():
        return False
    entry["future"].set_result(approved)
    return True


def list_pending(owner: str | None) -> list[dict]:
    return [{k: v for k, v in entry.items() if k != "future"}
            for entry in _pending.values() if entry["owner"] == owner]


async def run_shell(command: str, cwd: str | None = None) -> str:
    command = (command or "").strip()
    if not command:
        return "error: command is required"

    try:
        proc = await asyncio.create_subprocess_shell(
            command, cwd=cwd or None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as e:
        return f"error: failed to start command: {e}"

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return f"error: command timed out after {TIMEOUT_SECONDS}s"

    output = stdout.decode("utf-8", errors="replace")
    truncated = len(output) > MAX_OUTPUT_CHARS
    if truncated:
        output = output[:MAX_OUTPUT_CHARS]
    result = f"(exit code {proc.returncode})\n{output}"
    if truncated:
        result += f"\n... (truncated, output exceeded {MAX_OUTPUT_CHARS} characters)"
    return result
