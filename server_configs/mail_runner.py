from __future__ import annotations

import asyncio
import contextvars
import functools
import math
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor

import email_client
from generation_quota import MINUTE_SECONDS, QuotaRefusal

MAIL_WORKERS = 4
MAIL_CALLS_AT_ONCE_PER_ACCOUNT = 2


class MailRefused(RuntimeError):

    def __init__(self, refusal: QuotaRefusal):
        super().__init__(refusal.message)
        self.refusal = refusal


class MailRunner:

    def __init__(self, workers: int = MAIL_WORKERS, calls_at_once: int = MAIL_CALLS_AT_ONCE_PER_ACCOUNT,
                 clock=time.monotonic):
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mail")
        self.calls_at_once = calls_at_once
        self.clock = clock
        self._lock = threading.Lock()
        self._running: dict[str, int] = defaultdict(int)
        self._recent: dict[str, deque] = defaultdict(deque)

    def _rate_refusal(self, account: str, per_minute: int) -> QuotaRefusal | None:
        now = self.clock()
        recent = self._recent[account]
        while recent and now - recent[0] >= MINUTE_SECONDS:
            recent.popleft()
        if per_minute > 0 and len(recent) >= per_minute:
            wait = max(1, math.ceil(MINUTE_SECONDS - (now - recent[0])))
            return QuotaRefusal(f"this account can make {per_minute} mail requests a minute; "
                                f"try again in {wait} seconds", wait)
        recent.append(now)
        return None

    def _take(self, account: str, per_minute: int | None) -> None:
        with self._lock:
            if self._running[account] >= self.calls_at_once:
                raise MailRefused(QuotaRefusal(f"this account already has {self.calls_at_once} mail requests "
                                               "running; try again when they finish", 5))
            refusal = self._rate_refusal(account, per_minute) if per_minute is not None else None
            if refusal:
                raise MailRefused(refusal)
            self._running[account] += 1

    def _give_back(self, account: str) -> None:
        with self._lock:
            self._running[account] -= 1
            if self._running[account] <= 0:
                del self._running[account]

    async def run(self, account: str, per_minute: int | None, action, *args, **kwargs):
        self._take(account, per_minute)
        call = functools.partial(email_client.call_with_deadline, email_client.MAIL_DEADLINE_SECONDS,
                                 action, *args, **kwargs)
        try:
            submitted = self.executor.submit(contextvars.copy_context().run, call)
        except RuntimeError:
            self._give_back(account)
            raise
        submitted.add_done_callback(lambda _finished: self._give_back(account))
        return await asyncio.wrap_future(submitted)


mail_runner = MailRunner()
