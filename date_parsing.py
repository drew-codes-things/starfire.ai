from __future__ import annotations

import re
from datetime import datetime, timedelta

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_TIME_RE = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)
_IN_RE = re.compile(r"^in\s+(\d+)\s+(day|days|week|weeks)$", re.IGNORECASE)

def _extract_time(text: str, base: datetime) -> tuple[datetime, str]:
    m = _TIME_RE.search(text)
    if not m:
        return base.replace(hour=9, minute=0, second=0, microsecond=0), text
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    meridiem = (m.group(3) or "").lower()
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    remainder = (text[:m.start()] + text[m.end():]).strip()
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0), remainder

def parse_due_date(text: str, now: datetime | None = None) -> str:
    if not text or not text.strip():
        return ""
    now = now or datetime.now()
    raw = text.strip()
    lowered = raw.lower()

    candidate, remainder = _extract_time(lowered, now)

    if remainder == "today":
        return candidate.isoformat()
    if remainder == "tomorrow":
        return (candidate + timedelta(days=1)).isoformat()
    if remainder.startswith("next "):
        weekday_name = remainder[len("next "):].strip()
        if weekday_name in _WEEKDAYS:
            target = _WEEKDAYS.index(weekday_name)
            days_ahead = (target - candidate.weekday()) % 7 or 7
            return (candidate + timedelta(days=days_ahead)).isoformat()
    match = _IN_RE.match(remainder)
    if match:
        amount = int(match.group(1))
        delta = timedelta(weeks=amount) if match.group(2).startswith("week") else timedelta(days=amount)
        return (candidate + delta).isoformat()

    return raw
