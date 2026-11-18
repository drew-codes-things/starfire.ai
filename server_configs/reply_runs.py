from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Callable

from chat_stream import _sse_line

logger = logging.getLogger(__name__)

MAX_RUNNING_PER_ACCOUNT = 3
KEEP_FINISHED_SECONDS = 600
MAX_REPLY_BYTES = 8 * 1024 * 1024
MAX_KEPT_FINISHED_BYTES = 64 * 1024 * 1024
SSE_DATA_PREFIX = "data:"
TOO_LONG = "this reply grew too long to keep, so it was stopped"
BROKE_OFF = "the reply broke off on the server"


class TooManyReplies(RuntimeError):
    pass


@dataclass
class ReplyRun:
    id: str
    user_id: str
    session_id: str
    reply_index: int
    chunks: list[bytes] = field(default_factory=list)
    size: int = 0
    finished_at: float | None = None
    task: asyncio.Task | None = None
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)

    @property
    def finished(self) -> bool:
        return self.finished_at is not None

    def summary(self) -> dict:
        return {"id": self.id, "reply_index": self.reply_index}


def _events(chunks: list[bytes]):
    for chunk in chunks:
        line = chunk.decode("utf-8", errors="replace").strip()
        if not line.startswith(SSE_DATA_PREFIX):
            continue
        try:
            event = json.loads(line[len(SSE_DATA_PREFIX):].strip())
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def finished_reply(chunks: list[bytes], model: str) -> dict | None:
    parts: list[str] = []
    reply = {"role": "assistant", "model": model}
    for event in _events(chunks):
        if event.get("error"):
            return None
        if isinstance(event.get("delta"), str):
            parts.append(event["delta"])
        choice = event.get("model_choice")
        if isinstance(choice, dict) and isinstance(choice.get("model"), str):
            reply["model"] = choice["model"]
            reply["auto"] = choice.get("role")
        if event.get("untrusted_read"):
            reply["untrustedRead"] = True
    if not "".join(parts):
        return None
    return {**reply, "content": "".join(parts), "created": datetime.now(timezone.utc).isoformat()}


class ReplyRuns:

    def __init__(self, clock: Callable[[], float] = time.monotonic, max_running: int = MAX_RUNNING_PER_ACCOUNT,
                 keep_finished_seconds: float = KEEP_FINISHED_SECONDS, max_reply_bytes: int = MAX_REPLY_BYTES,
                 max_kept_bytes: int = MAX_KEPT_FINISHED_BYTES):
        self.clock = clock
        self.max_running = max_running
        self.keep_finished_seconds = keep_finished_seconds
        self.max_reply_bytes = max_reply_bytes
        self.max_kept_bytes = max_kept_bytes
        self._runs: dict[str, ReplyRun] = {}

    def _forget_old(self) -> None:
        now = self.clock()
        for run_id, run in list(self._runs.items()):
            if run.finished and now - run.finished_at > self.keep_finished_seconds:
                del self._runs[run_id]
        self._forget_beyond_the_kept_byte_cap()

    def _forget_beyond_the_kept_byte_cap(self) -> None:
        finished = sorted((run for run in self._runs.values() if run.finished), key=lambda run: run.finished_at)
        kept_bytes = sum(run.size for run in finished)
        for run in finished:
            if kept_bytes <= self.max_kept_bytes:
                return
            kept_bytes -= run.size
            del self._runs[run.id]

    def _running(self, user_id: str) -> list[ReplyRun]:
        return [run for run in self._runs.values() if run.user_id == user_id and not run.finished]

    def start(self, user_id: str, session_id: str, reply_index: int, chunks: AsyncIterator[bytes],
              on_finished: Callable[[ReplyRun, bool], None]) -> ReplyRun:
        self._forget_old()
        if len(self._running(user_id)) >= self.max_running:
            raise TooManyReplies(f"at most {self.max_running} replies can be written at once; "
                                 "wait for one to finish or stop it")
        run = ReplyRun(id=uuid.uuid4().hex, user_id=user_id, session_id=session_id, reply_index=reply_index)
        self._runs[run.id] = run
        run.task = asyncio.create_task(self._drive(run, chunks, on_finished))
        return run

    async def _add(self, run: ReplyRun, chunk: bytes) -> None:
        async with run.changed:
            run.chunks.append(chunk)
            run.size += len(chunk)
            run.changed.notify_all()

    async def _finish(self, run: ReplyRun) -> None:
        async with run.changed:
            run.finished_at = self.clock()
            run.changed.notify_all()

    async def _drive(self, run: ReplyRun, chunks: AsyncIterator[bytes],
                     on_finished: Callable[[ReplyRun, bool], None]) -> None:
        completed = False
        try:
            async for chunk in chunks:
                if run.size + len(chunk) > self.max_reply_bytes:
                    await self._add(run, _sse_line({"error": TOO_LONG}))
                    break
                await self._add(run, chunk)
            else:
                completed = True
        except asyncio.CancelledError:
            logger.info("reply %s was stopped", run.id)
        except Exception:
            logger.exception("reply %s failed while it was being written", run.id)
            await self._add(run, _sse_line({"error": BROKE_OFF}))
        finally:
            await _close(chunks)
            _report_finish(on_finished, run, completed)
            await self._finish(run)

    def get(self, user_id: str, run_id: str) -> ReplyRun | None:
        self._forget_old()
        run = self._runs.get(run_id)
        return run if run and run.user_id == user_id else None

    def running_for_session(self, user_id: str, session_id: str) -> ReplyRun | None:
        self._forget_old()
        return next((run for run in self._running(user_id) if run.session_id == session_id), None)

    def stop(self, user_id: str, run_id: str) -> bool:
        run = self.get(user_id, run_id)
        if not run or run.finished or not run.task:
            return False
        run.task.cancel()
        return True

    async def follow(self, run: ReplyRun) -> AsyncIterator[bytes]:
        sent = 0
        while True:
            async with run.changed:
                await run.changed.wait_for(lambda: len(run.chunks) > sent or run.finished)
                fresh, done = run.chunks[sent:], run.finished
            sent += len(fresh)
            for chunk in fresh:
                yield chunk
            if done:
                return


async def _close(chunks: AsyncIterator[bytes]) -> None:
    close = getattr(chunks, "aclose", None)
    if close is None:
        return
    try:
        await close()
    except Exception:
        logger.exception("closing a finished reply stream failed")


def _report_finish(on_finished: Callable[[ReplyRun, bool], None], run: ReplyRun, completed: bool) -> None:
    try:
        on_finished(run, completed)
    except Exception:
        logger.exception("saving reply %s to its chat failed", run.id)
