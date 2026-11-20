from __future__ import annotations

import asyncio
import re
import time
import uuid

OPEN_TIMEOUT_SECONDS = 180
MAX_URL_CHARS = 2000
MAX_BROWSER_CHARS = 40
MAX_DETAIL_CHARS = 300
MAX_TAB = 2**53
WEB_SCHEMES = ("http://", "https://")
MAX_HOST_CHARS = 253
BROWSER_NAMES = ("Chrome", "Edge", "Brave", "Opera", "Vivaldi", "Chromium", "Firefox")
BROWSER_NAME_FORMAT = re.compile(rf"(?:{'|'.join(BROWSER_NAMES)})(?: [2-9])?")
REASON_TEXTS = {
    "declined": "the user said no in Arty Desktop",
    "timed_out": "nobody allowed it in Arty Desktop in time",
    "abandoned": "Arty Desktop dropped the request before anyone answered it",
    "busy": "Arty Desktop was already waiting on another step",
    "bad_request": "Arty Desktop refused the address or the browser name",
    "no_browser": "no browser is connected to Arty Desktop",
    "which_browser": "more than one browser is connected and none was named; ask the user which browser to use",
    "not_connected": "that browser is not connected to Arty Desktop",
    "unavailable": "opening pages is not available in Arty Desktop right now",
    "not_running": "Arty Desktop is not running on their computer",
    "not_linked": "starfire Desktop is not linked to Arty Desktop",
    "failed": "opening the page failed",
}
DEFAULT_REASON_TEXT = "it failed for a reason starfire Desktop could not name"

_pending: dict[str, dict] = {}


class NotWaiting(LookupError):
    pass


class AlreadyClaimed(LookupError):
    pass


class BadAnswer(ValueError):
    pass


def is_browser_name(name) -> bool:
    return isinstance(name, str) and len(name) <= MAX_BROWSER_CHARS and BROWSER_NAME_FORMAT.fullmatch(name) is not None


def stage(url: str, browser: str, owner: str | None) -> str:
    request_id = uuid.uuid4().hex[:16]
    _pending[request_id] = {"url": url, "browser": browser, "owner": owner, "claimed": False, "created": time.time(),
                            "future": asyncio.get_running_loop().create_future()}
    return request_id


def _own_entry(request_id: str, owner: str | None) -> dict:
    entry = _pending.get(request_id)
    if entry is None or entry["owner"] != owner or entry["future"].done():
        raise NotWaiting(request_id)
    return entry


def claim(request_id: str, owner: str | None) -> dict:
    entry = _own_entry(request_id, owner)
    if entry["claimed"]:
        raise AlreadyClaimed(request_id)
    entry["claimed"] = True
    return {"url": entry["url"], "browser": entry["browser"]}


def _checked_open(outcome: dict) -> None:
    browser = outcome.get("browser", "")
    if browser and not is_browser_name(browser):
        raise BadAnswer("the browser must be one of " + ", ".join(BROWSER_NAMES))
    tab = outcome.get("tab")
    if tab is not None and (isinstance(tab, bool) or not isinstance(tab, int) or not 0 <= tab <= MAX_TAB):
        raise BadAnswer("the tab must be a whole number that is not negative")


def resolve(request_id: str, owner: str | None, outcome: dict) -> bool:
    try:
        entry = _own_entry(request_id, owner)
    except NotWaiting:
        return False
    if outcome.get("ok") is True:
        _checked_open(outcome)
    entry["future"].set_result(outcome)
    return True


async def wait_for_open(request_id: str) -> dict:
    entry = _pending.get(request_id)
    if entry is None:
        return {"ok": False, "reason": "timed_out"}
    try:
        return await asyncio.wait_for(asyncio.shield(entry["future"]), timeout=OPEN_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return {"ok": False, "reason": "timed_out"}
    finally:
        _pending.pop(request_id, None)


def site_is_shown_in_full(host: str) -> bool:
    try:
        ascii_host = host.encode("idna")
    except UnicodeError:
        return False
    return 0 < len(ascii_host) <= MAX_HOST_CHARS
