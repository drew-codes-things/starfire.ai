from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

MINUTE_SECONDS = 60


@dataclass(frozen=True)
class QuotaRefusal:
    message: str
    retry_after_seconds: int


def _seconds_to_next_utc_day(now: float) -> int:
    current = datetime.fromtimestamp(now, timezone.utc)
    tomorrow = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, math.ceil(tomorrow.timestamp() - now))


class GenerationQuota:

    def __init__(self, clock=time.time):
        self.clock = clock
        self._recent: dict[str, deque] = defaultdict(deque)
        self._daily: dict[str, tuple[str, int]] = {}

    def _today(self, now: float) -> str:
        return datetime.fromtimestamp(now, timezone.utc).date().isoformat()

    def take(self, account_id: str, per_minute: int, per_day: int, counts_per_minute: bool = True,
             counts_per_day: bool = True) -> QuotaRefusal | None:
        now = self.clock()
        recent = self._recent[account_id]
        while recent and now - recent[0] >= MINUTE_SECONDS:
            recent.popleft()
        day, used_today = self._daily.get(account_id, (self._today(now), 0))
        if day != self._today(now):
            day, used_today = self._today(now), 0
        if per_day > 0 and used_today >= per_day:
            return QuotaRefusal(f"this account has used its {per_day} model replies for today; "
                                "the count starts again at midnight UTC", _seconds_to_next_utc_day(now))
        if counts_per_minute and per_minute > 0 and len(recent) >= per_minute:
            wait = max(1, math.ceil(MINUTE_SECONDS - (now - recent[0])))
            return QuotaRefusal(f"this account can ask for {per_minute} model replies a minute; "
                                f"try again in {wait} seconds", wait)
        if counts_per_minute:
            recent.append(now)
        self._daily[account_id] = (day, used_today + (1 if counts_per_day else 0))
        return None

    def give_back_day(self, account_id: str) -> None:
        day, used_today = self._daily.get(account_id, ("", 0))
        if used_today > 0:
            self._daily[account_id] = (day, used_today - 1)
