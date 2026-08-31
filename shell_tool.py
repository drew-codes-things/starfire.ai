"""General-purpose shell execution — the "work like Claude Code" tool: one
shell tool the model can call directly during a chat (or a scheduled
automation, since tasks already carry their own enabled_builtin_tools list),
not a curated subset of commands and not a separate SSH-specific tool.
Running `ssh host 'command'` through this tool covers SSH the same way
Claude Code itself uses a single Bash tool for everything rather than a
dedicated SSH tool.

No sandbox underneath this — it runs `command` as this OS user, with only a
timeout and an output-size cap as guardrails. That matches odysseus-dev's own
services/shell/service.py, which is equally unsandboxed by its own
THREAT_MODEL.md's admission: shell access there is treated as an
authorization problem (admin-only), not a containment one. The mitigation
here is the same shape: this tool is OFF by default and only reachable once
you explicitly enable "Run shell commands" in Settings -> Tools (or add it to
a specific automation's enabled tools) — the same off-by-default, explicit
opt-in pattern as every other high-privilege tool in this app. Nothing
downstream stops it from doing real damage if the model is tricked into
misusing it (e.g. by text it read from an untrusted webpage or email), so
only enable it in a chat/automation that doesn't also read untrusted content.
"""

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
