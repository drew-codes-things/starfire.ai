"""Scoped GitHub access via the `gh` CLI — runs ONLY the `gh` binary with the
arguments given, never an arbitrary shell string. Meaningfully narrower than
routing this through run_shell/shell_tool.py: this tool can list issues,
open PRs, check CI status, etc., but can't `rm -rf` anything, can't chain
commands with `&&`/`;`/pipes, and can't touch any binary other than `gh`
itself. Toggleable independently of the general shell tool for that reason —
enabling GitHub access doesn't require also enabling full shell access.

Requires the `gh` CLI installed and authenticated (`gh auth login`) on this
machine — this tool doesn't manage credentials itself, same separation
providers.py/email_client.py use (the caller already has whatever `gh` is
already configured with).
"""

from __future__ import annotations

import asyncio
import shutil

TIMEOUT_SECONDS = 60
MAX_OUTPUT_CHARS = 8000


async def run_gh(args: list[str]) -> str:
    if not args:
        return "error: args is required, e.g. [\"issue\", \"list\"]"
    if shutil.which("gh") is None:
        return "error: the 'gh' CLI is not installed on this machine"

    try:
        proc = await asyncio.create_subprocess_exec(
            "gh", *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as e:
        return f"error: failed to start gh: {e}"

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return f"error: gh command timed out after {TIMEOUT_SECONDS}s"

    output = stdout.decode("utf-8", errors="replace")
    truncated = len(output) > MAX_OUTPUT_CHARS
    if truncated:
        output = output[:MAX_OUTPUT_CHARS]
    result = f"(exit code {proc.returncode})\n{output}"
    if truncated:
        result += f"\n... (truncated, output exceeded {MAX_OUTPUT_CHARS} characters)"
    return result
