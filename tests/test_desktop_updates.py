import asyncio
import json
import os
import sys
import tracemalloc
import zlib

import bcrypt
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import auth
import auth_routes
import desktop_updates
from auth import SessionStore, UserStore
from config import config
from version import VERSION

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}
RELEASE_PAGE = "https://github.com/drew-codes-things/starfire.ai/releases/tag/"


def release(tag, draft=False, prerelease=False, **extra):
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease,
            "html_url": "https://example.org/elsewhere", **extra}


PUBLISHED = [release("v1.2.0"), release("desktop-v1.0.0"), release("v1.1.0"), release("desktop-v1.1.0"),
             release("desktop-v0.9.0"), release("Beta")]


class Chunks(httpx.AsyncByteStream):

    def __init__(self, data):
        self.data = data

    async def __aiter__(self):
        for start in range(0, len(self.data), 65536):
            yield self.data[start:start + 65536]


def answering(status=200, body=None, content=None, seen=None):
    def handle(request):
        if seen is not None:
            seen.append(request)
        payload = content if content is not None else json.dumps(PUBLISHED if body is None else body).encode()
        return httpx.Response(status, stream=Chunks(payload))
    return httpx.MockTransport(handle)


def fetched(transport):
    return asyncio.run(desktop_updates.fetch_latest(transport))


class Clock:

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_the_newest_desktop_release_is_picked_from_the_desktop_tags_only():
    assert desktop_updates.latest_desktop_release(PUBLISHED) == {"version": "1.1.0", "url": RELEASE_PAGE + "desktop-v1.1.0"}


def test_versions_compare_as_numbers_not_as_text():
    found = desktop_updates.latest_desktop_release([release("desktop-v1.9.0"), release("desktop-v1.10.0")])
    assert found == {"version": "1.10.0", "url": RELEASE_PAGE + "desktop-v1.10.0"}


def test_drafts_pre_releases_odd_tags_and_odd_entries_are_skipped():
    releases = [release("desktop-v9.0.0", draft=True), release("desktop-v8.0.0", prerelease=True),
                release("desktop-v7.0.0-beta"), release("desktop-v6"), release("desktop-vx.y"),
                release("desktop-v5.0.0/../../evil"), {"tag_name": 5}, "desktop-v4.0.0", None, [],
                release("v3.0.0"), release("desktop-v1.0.0")]
    assert desktop_updates.latest_desktop_release(releases) == {"version": "6", "url": RELEASE_PAGE + "desktop-v6"}
    assert desktop_updates.latest_desktop_release([release("v2.0.0")]) is None
    assert desktop_updates.latest_desktop_release([]) is None


def test_only_ascii_digits_make_a_desktop_version():
    assert desktop_updates.latest_desktop_release([release("desktop-v\u0661.\u0662")]) is None
    found = desktop_updates.latest_desktop_release([release("desktop-v\u0661.\u0662"), release("desktop-v\uff19.0"), release("desktop-v1.0")])
    assert found == {"version": "1.0", "url": RELEASE_PAGE + "desktop-v1.0"}


def test_a_release_list_that_is_not_a_list_is_a_failed_check():
    for answer in [{"message": "Not Found"}, "desktop-v1.0.0", None, 5]:
        with pytest.raises(desktop_updates.ReleaseCheckFailed):
            desktop_updates.latest_desktop_release(answer)


def test_the_check_is_one_https_request_with_no_cookies_or_ids():
    seen = []
    assert fetched(answering(seen=seen)) == {"version": "1.1.0", "url": RELEASE_PAGE + "desktop-v1.1.0"}
    assert len(seen) == 1
    sent = seen[0]
    assert sent.method == "GET"
    assert str(sent.url).startswith("https://api.github.com/repos/drew-codes-things/starfire.ai/releases")
    assert sent.headers["user-agent"] == f"starfire/{VERSION}"
    assert sent.headers["accept-encoding"] == "identity"
    for private in ["cookie", "authorization", "x-starfire-client"]:
        assert private not in sent.headers


@pytest.mark.parametrize("transport", [
    answering(status=403, body={"message": "API rate limit exceeded"}),
    answering(status=500),
    answering(content=b"<html>not json</html>"),
    answering(content=b"{\"truncated\": "),
    answering(body={"message": "Not Found"}),
    answering(content=b"[" * 100_000 + b"]" * 100_000),
])
def test_a_refused_or_unreadable_answer_is_a_failed_check(transport):
    with pytest.raises(desktop_updates.ReleaseCheckFailed):
        fetched(transport)


def test_an_answer_larger_than_the_cap_is_a_failed_check():
    huge = json.dumps([release("desktop-v2.0.0", body="x" * desktop_updates.MAX_RESPONSE_BYTES)]).encode()
    with pytest.raises(desktop_updates.ReleaseCheckFailed, match="256 KB"):
        fetched(answering(content=huge))


def compressed_spaces(megabytes):
    compressor = zlib.compressobj(9, zlib.DEFLATED, 31)
    return b"".join(compressor.compress(b" " * (1 << 20)) for _ in range(megabytes)) + compressor.flush()


def test_a_compressed_bomb_is_refused_without_unpacking_it():
    bomb = compressed_spaces(512)

    def handle(request):
        return httpx.Response(200, stream=Chunks(bomb), headers={"content-encoding": "gzip"})
    tracemalloc.start()
    try:
        with pytest.raises(desktop_updates.ReleaseCheckFailed):
            fetched(httpx.MockTransport(handle))
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024


def test_a_small_compressed_answer_is_refused_too():
    compressor = zlib.compressobj(9, zlib.DEFLATED, 31)
    packed = compressor.compress(json.dumps(PUBLISHED).encode()) + compressor.flush()

    def handle(request):
        return httpx.Response(200, stream=Chunks(packed), headers={"content-encoding": "gzip"})
    with pytest.raises(desktop_updates.ReleaseCheckFailed, match="compressed"):
        fetched(httpx.MockTransport(handle))


def test_the_default_transport_reaches_only_public_addresses(monkeypatch):
    seen = []
    monkeypatch.setattr(desktop_updates, "public_only_transport", lambda: answering(seen=seen))
    assert asyncio.run(desktop_updates.fetch_latest()) == {"version": "1.1.0", "url": RELEASE_PAGE + "desktop-v1.1.0"}
    assert len(seen) == 1


def test_a_network_error_outside_httpx_is_a_failed_check():
    def handle(request):
        raise OSError("name resolution failed")
    with pytest.raises(desktop_updates.ReleaseCheckFailed, match="name resolution failed"):
        fetched(httpx.MockTransport(handle))


def server_releases(count, start=100):
    return [release(f"v1.{number}.0") for number in range(start, start + count)]


def paged(pages, seen):
    def handle(request):
        seen.append(request)
        number = int(request.url.params["page"])
        return httpx.Response(200, stream=Chunks(json.dumps(pages[number - 1] if number <= len(pages) else []).encode()))
    return httpx.MockTransport(handle)


def test_a_desktop_release_behind_a_full_page_of_server_releases_is_still_found():
    seen = []
    pages = [server_releases(desktop_updates.RELEASES_PER_PAGE), server_releases(5, 200) + [release("desktop-v1.1.0")]]
    assert fetched(paged(pages, seen)) == {"version": "1.1.0", "url": RELEASE_PAGE + "desktop-v1.1.0"}
    assert [request.url.params["page"] for request in seen] == ["1", "2"]
    assert {request.url.params["per_page"] for request in seen} == {str(desktop_updates.RELEASES_PER_PAGE)}


def test_paging_stops_at_a_full_page_that_holds_a_desktop_release():
    seen = []
    first = server_releases(desktop_updates.RELEASES_PER_PAGE - 1) + [release("desktop-v1.1.0")]
    later = [server_releases(desktop_updates.RELEASES_PER_PAGE, 100 * page) + [release(f"desktop-v9.{page}.0")]
             for page in range(2, 6)]
    assert len(first) == desktop_updates.RELEASES_PER_PAGE
    assert fetched(paged([first, *later], seen)) == {"version": "1.1.0", "url": RELEASE_PAGE + "desktop-v1.1.0"}
    assert len(seen) == 1


def test_paging_stops_at_a_short_page_and_at_the_page_limit():
    seen = []
    assert fetched(paged([server_releases(3)], seen)) is None
    assert len(seen) == 1
    seen.clear()
    full = [server_releases(desktop_updates.RELEASES_PER_PAGE, 100 * page) for page in range(1, 20)]
    assert fetched(paged(full, seen)) is None
    assert len(seen) == desktop_updates.MAX_RELEASE_PAGES


def test_a_slow_answer_gives_up_after_the_time_limit(monkeypatch):
    monkeypatch.setattr(desktop_updates, "TIMEOUT_SECONDS", 0.05)

    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, content=b"[]")
    with pytest.raises(desktop_updates.ReleaseCheckFailed, match="time"):
        fetched(httpx.MockTransport(slow))


def test_a_redirect_is_not_followed():
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://example.org/releases"})
    with pytest.raises(desktop_updates.ReleaseCheckFailed):
        fetched(httpx.MockTransport(handle))
    assert len(seen) == 1


def test_the_answer_is_kept_for_six_hours():
    calls = []
    clock = Clock()

    async def fetch():
        calls.append(clock.now)
        return {"version": f"1.{len(calls)}.0", "url": RELEASE_PAGE + f"desktop-v1.{len(calls)}.0"}
    releases = desktop_updates.DesktopReleases(fetch=fetch, clock=clock)
    assert asyncio.run(releases.latest())["version"] == "1.1.0"
    clock.now += 6 * 60 * 60 - 1
    assert asyncio.run(releases.latest())["version"] == "1.1.0"
    clock.now += 1
    assert asyncio.run(releases.latest())["version"] == "1.2.0"
    assert len(calls) == 2


def test_a_failed_check_logs_one_line_keeps_the_last_answer_and_waits_before_trying_again(caplog):
    clock = Clock()
    outcomes = [{"version": "1.1.0", "url": RELEASE_PAGE + "desktop-v1.1.0"}, desktop_updates.ReleaseCheckFailed("GitHub answered 403"),
                httpx.ConnectError("offline")]

    async def fetch():
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    releases = desktop_updates.DesktopReleases(fetch=fetch, clock=clock)
    first = asyncio.run(releases.latest())
    clock.now += desktop_updates.CHECK_EVERY_SECONDS
    with caplog.at_level("INFO", logger=desktop_updates.logger.name):
        assert asyncio.run(releases.latest()) == first
    assert [record.levelname for record in caplog.records] == ["INFO"]
    assert "GitHub answered 403" in caplog.records[0].getMessage()
    clock.now += desktop_updates.RETRY_AFTER_FAILURE_SECONDS - 1
    assert asyncio.run(releases.latest()) == first
    assert len(outcomes) == 1
    clock.now += 1
    assert asyncio.run(releases.latest()) == first
    assert outcomes == []


def test_a_failed_first_check_means_no_release_is_known():
    async def fetch():
        raise desktop_updates.ReleaseCheckFailed("the release list is not JSON")
    assert asyncio.run(desktop_updates.DesktopReleases(fetch=fetch, clock=Clock()).latest()) is None


def test_callers_at_the_same_moment_share_one_check():
    calls = []

    async def fetch():
        calls.append(1)
        await asyncio.sleep(0.01)
        return {"version": "1.1.0", "url": RELEASE_PAGE + "desktop-v1.1.0"}

    async def together():
        releases = desktop_updates.DesktopReleases(fetch=fetch, clock=Clock())
        return await asyncio.gather(*(releases.latest() for _ in range(5)))
    assert len(set(json.dumps(answer) for answer in asyncio.run(together()))) == 1
    assert len(calls) == 1


@pytest.fixture
def app(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    users, sessions = UserStore(data_dir), SessionStore(data_dir)
    monkeypatch.setattr(config, "data_dir", data_dir)
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(auth_routes, "known_addresses", auth.KnownAddresses(data_dir))
    hashed = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    users.upsert("sam@example.com", "Sam", "user", hashed)
    application = FastAPI()
    auth.install(application, users, sessions)
    application.include_router(auth_routes.router)
    application.include_router(desktop_updates.router)
    return application


@pytest.fixture
def checks(monkeypatch):
    calls = []

    async def fetch():
        calls.append(1)
        return {"version": "1.1.0", "url": RELEASE_PAGE + "desktop-v1.1.0"}
    monkeypatch.setattr(desktop_updates, "releases", desktop_updates.DesktopReleases(fetch=fetch))
    return calls


def signed_in(app):
    client = TestClient(app, base_url="https://testserver", headers=HEADERS)
    assert client.post("/api/auth/login", json={"email": "sam@example.com", "password": PASSWORD}).status_code == 200
    return client


def test_a_signed_in_page_learns_the_newest_desktop_release(app, checks):
    answer = signed_in(app).get("/api/desktop/latest")
    assert answer.status_code == 200
    assert answer.json() == {"check": True, "version": "1.1.0", "url": RELEASE_PAGE + "desktop-v1.1.0"}


def test_a_signed_out_caller_is_refused_and_nothing_is_fetched(app, checks):
    answer = TestClient(app, base_url="https://testserver", headers=HEADERS).get("/api/desktop/latest")
    assert answer.status_code == 401
    assert checks == []


def test_the_setting_turns_the_check_off_and_nothing_is_fetched(app, checks, monkeypatch):
    monkeypatch.setattr(config, "desktop_update_check", False)
    answer = signed_in(app).get("/api/desktop/latest")
    assert answer.json() == {"check": False, "version": None, "url": None}
    assert checks == []


def test_the_check_is_on_unless_the_server_turns_it_off(monkeypatch):
    monkeypatch.delenv("DESKTOP_UPDATE_CHECK", raising=False)
    assert type(config)(_env_file=None).desktop_update_check is True
    monkeypatch.setenv("DESKTOP_UPDATE_CHECK", "false")
    assert type(config)(_env_file=None).desktop_update_check is False


def test_an_unknown_release_answers_with_no_version(app, monkeypatch):
    async def fetch():
        raise desktop_updates.ReleaseCheckFailed("offline")
    monkeypatch.setattr(desktop_updates, "releases", desktop_updates.DesktopReleases(fetch=fetch))
    assert signed_in(app).get("/api/desktop/latest").json() == {"check": True, "version": None, "url": None}


def test_the_server_serves_the_route_before_the_static_files():
    with open(os.path.join(ROOT, "server.py"), encoding="utf-8") as source:
        text = source.read()
    included = text.index("app.include_router(desktop_updates.router)")
    assert included < text.index("app.mount(\"/\"")
    with open(os.path.join(ROOT, ".env.example"), encoding="utf-8") as example:
        assert "DESKTOP_UPDATE_CHECK=true" in example.read().splitlines()
