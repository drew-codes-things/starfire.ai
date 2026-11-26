from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Awaitable, Callable

import httpx
from fastapi import APIRouter

from config import config
from public_fetch import public_only_transport
from version import VERSION

logger = logging.getLogger(__name__)

RELEASES_URL = "https://api.github.com/repos/drew-codes-things/starfire.ai/releases"
RELEASES_PER_PAGE = 20
MAX_RELEASE_PAGES = 5
RELEASE_PAGE = "https://github.com/drew-codes-things/starfire.ai/releases/tag/"
DESKTOP_TAG_PREFIX = "desktop-v"
DESKTOP_VERSION = re.compile(r"[0-9]{1,6}(?:\.[0-9]{1,6}){0,3}")
CHECK_EVERY_SECONDS = 6 * 60 * 60
RETRY_AFTER_FAILURE_SECONDS = 15 * 60
TIMEOUT_SECONDS = 5.0
MAX_RESPONSE_BYTES = 256 * 1024
PLAIN_ENCODINGS = frozenset({"", "identity"})
REQUEST_HEADERS = {"Accept": "application/vnd.github+json", "Accept-Encoding": "identity", "User-Agent": f"starfire/{VERSION}"}

router = APIRouter(prefix="/api")


class ReleaseCheckFailed(Exception):
    pass


def desktop_release(entry) -> tuple[tuple[int, ...], str, str] | None:
    if not isinstance(entry, dict) or entry.get("draft") or entry.get("prerelease"):
        return None
    tag = entry.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith(DESKTOP_TAG_PREFIX):
        return None
    version = tag[len(DESKTOP_TAG_PREFIX):]
    if not DESKTOP_VERSION.fullmatch(version):
        return None
    return tuple(int(part) for part in version.split(".")), version, tag


def latest_desktop_release(releases) -> dict | None:
    if not isinstance(releases, list):
        raise ReleaseCheckFailed("the release list is not a list")
    found = [release for release in map(desktop_release, releases) if release is not None]
    if not found:
        return None
    _, version, tag = max(found, key=lambda release: release[0])
    return {"version": version, "url": RELEASE_PAGE + tag}


async def read_capped(response: httpx.Response) -> bytes:
    if response.headers.get("content-encoding", "").strip().lower() not in PLAIN_ENCODINGS:
        raise ReleaseCheckFailed("GitHub sent a compressed answer")
    body = bytearray()
    async for chunk in response.aiter_raw():
        body.extend(chunk)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ReleaseCheckFailed("the release list is larger than 256 KB")
    return bytes(body)


def parsed_releases(body: bytes) -> list:
    try:
        releases = json.loads(body)
    except (ValueError, RecursionError) as error:
        raise ReleaseCheckFailed(f"the release list is not JSON: {error}") from error
    if not isinstance(releases, list):
        raise ReleaseCheckFailed("the release list is not a list")
    return releases


async def read_release_page(client: httpx.AsyncClient, page: int) -> list:
    params = {"per_page": RELEASES_PER_PAGE, "page": page}
    async with client.stream("GET", RELEASES_URL, params=params, headers=REQUEST_HEADERS) as response:
        if response.status_code != 200:
            raise ReleaseCheckFailed(f"GitHub answered {response.status_code}")
        body = await read_capped(response)
    return parsed_releases(body)


async def read_latest(transport: httpx.AsyncBaseTransport) -> dict | None:
    async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
        for page in range(1, MAX_RELEASE_PAGES + 1):
            releases = await read_release_page(client, page)
            found = latest_desktop_release(releases)
            if found is not None or len(releases) < RELEASES_PER_PAGE:
                return found
    return None


async def fetch_latest(transport: httpx.AsyncBaseTransport | None = None) -> dict | None:
    try:
        return await asyncio.wait_for(read_latest(transport or public_only_transport()), TIMEOUT_SECONDS)
    except asyncio.TimeoutError as error:
        raise ReleaseCheckFailed(f"GitHub did not answer in time ({TIMEOUT_SECONDS:g} s)") from error
    except (httpx.HTTPError, OSError) as error:
        raise ReleaseCheckFailed(f"could not reach GitHub: {error}") from error


class DesktopReleases:

    def __init__(self, fetch: Callable[[], Awaitable[dict | None]] = fetch_latest,
                 clock: Callable[[], float] = time.monotonic):
        self._fetch = fetch
        self._clock = clock
        self._lock = asyncio.Lock()
        self._latest: dict | None = None
        self._next_check_at: float | None = None

    def _is_fresh(self) -> bool:
        return self._next_check_at is not None and self._clock() < self._next_check_at

    async def latest(self) -> dict | None:
        async with self._lock:
            if self._is_fresh():
                return self._latest
            try:
                self._latest = await self._fetch()
                self._next_check_at = self._clock() + CHECK_EVERY_SECONDS
            except (ReleaseCheckFailed, httpx.HTTPError) as error:
                logger.info("could not check for a new starfire Desktop release: %s", error)
                self._next_check_at = self._clock() + RETRY_AFTER_FAILURE_SECONDS
            return self._latest


releases = DesktopReleases()


@router.get("/desktop/latest")
async def latest_desktop():
    if not config.desktop_update_check:
        return {"check": False, "version": None, "url": None}
    found = await releases.latest()
    return {"check": True, "version": found["version"] if found else None, "url": found["url"] if found else None}
