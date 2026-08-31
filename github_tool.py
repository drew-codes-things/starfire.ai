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
