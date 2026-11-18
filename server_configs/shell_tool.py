from __future__ import annotations

import asyncio
import os
import time
import uuid

from host_sandbox import CappedRun, HostLimits, run_capped, scrubbed_env, shell_argv

TIMEOUT_SECONDS = 120
MAX_OUTPUT_CHARS = 8000
MAX_OUTPUT_BYTES = 4 * MAX_OUTPUT_CHARS
APPROVAL_TIMEOUT_SECONDS = 300

_pending: dict[str, dict] = {}


def stage(command: str, cwd: str | None, owner: str | None, tool: str = "run_shell", untrusted: bool = False,
          unsandboxed: bool = False) -> str:
    pending_id = uuid.uuid4().hex[:12]
    _pending[pending_id] = {
        "id": pending_id, "owner": owner, "command": command, "cwd": cwd or "", "tool": tool, "untrusted": untrusted,
        "unsandboxed": unsandboxed, "created": time.time(), "future": asyncio.get_running_loop().create_future(),
    }
    return pending_id


def pending_tool(pending_id: str, owner: str | None) -> str | None:
    entry = _pending.get(pending_id)
    return entry["tool"] if entry and entry["owner"] == owner else None


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


async def run_shell(command: str, cwd: str | None = None, limits: HostLimits = HostLimits()) -> str:
    command = (command or "").strip()
    if not command:
        return "error: command is required"
    workdir = cwd or limits.workspace or os.getcwd()
    if limits.workspace:
        os.makedirs(limits.workspace, exist_ok=True)
    if not os.path.isdir(workdir):
        return f"error: failed to start command: there is no folder {workdir}"
    env = scrubbed_env()
    argv, outside_cwd = shell_argv(command, workdir, limits, env)
    try:
        run = await run_capped(argv, outside_cwd, env, TIMEOUT_SECONDS, MAX_OUTPUT_BYTES)
    except OSError as e:
        return f"error: failed to start command: {e}"
    if run.timed_out:
        return f"error: command timed out after {TIMEOUT_SECONDS}s"
    return format_output(run)


def format_output(run: CappedRun) -> str:
    output = run.output.decode("utf-8", errors="replace")
    truncated = run.truncated or len(output) > MAX_OUTPUT_CHARS
    output = output[:MAX_OUTPUT_CHARS]
    result = f"(exit code {run.returncode})\n{output}"
    if truncated:
        result += f"\n... (truncated, output exceeded {MAX_OUTPUT_CHARS} characters)"
    return result
