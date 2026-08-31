from __future__ import annotations

import asyncio

TIMEOUT_SECONDS = 120
MAX_OUTPUT_CHARS = 8000

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
