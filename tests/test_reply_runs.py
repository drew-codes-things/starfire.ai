import asyncio
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import chat_stream
from chat_session_store import ChatSessionStore
from reply_runs import ReplyRuns, TooManyReplies, finished_reply

MODEL = "general:14b"
WAIT_SECONDS = 5


def line(event):
    return chat_stream._sse_line(event)


async def held_stream(release: asyncio.Event, parts):
    for part in parts[:1]:
        yield line({"delta": part})
    await release.wait()
    for part in parts[1:]:
        yield line({"delta": part})
    yield line({"done": True})


def test_a_reply_keeps_going_after_its_reader_leaves_and_reports_it_finished():
    async def scenario():
        runs = ReplyRuns()
        release = asyncio.Event()
        finished = []
        run = runs.start("u1", "s1", 1, held_stream(release, ["Hello", " world"]),
                         lambda r, completed: finished.append((completed, finished_reply(r.chunks, MODEL))))
        reader = runs.follow(run)
        assert json.loads((await reader.__anext__()).decode()[len("data:"):]) == {"delta": "Hello"}
        await reader.aclose()
        release.set()
        await asyncio.wait_for(run.task, WAIT_SECONDS)
        return run, finished, [chunk async for chunk in runs.follow(run)]

    run, finished, replayed = asyncio.run(scenario())
    assert finished[0][0] is True
    assert finished[0][1]["content"] == "Hello world" and finished[0][1]["model"] == MODEL
    assert len(replayed) == 3 and run.finished


def test_a_stopped_reply_is_reported_unfinished():
    async def scenario():
        runs = ReplyRuns()
        finished = []
        run = runs.start("u1", "s1", 1, held_stream(asyncio.Event(), ["Hel"]), lambda r, c: finished.append(c))
        await asyncio.sleep(0)
        assert runs.stop("someone-else", run.id) is False
        assert runs.stop("u1", run.id) is True
        await asyncio.wait_for(asyncio.gather(run.task, return_exceptions=True), WAIT_SECONDS)
        return finished

    assert asyncio.run(scenario()) == [False]


def test_an_account_can_only_have_a_few_replies_running_at_once():
    async def scenario():
        runs = ReplyRuns(max_running=2)
        release = asyncio.Event()
        for index in range(2):
            runs.start("u1", f"s{index}", 1, held_stream(release, ["x"]), lambda r, c: None)
        with pytest.raises(TooManyReplies):
            runs.start("u1", "s9", 1, held_stream(release, ["x"]), lambda r, c: None)
        other = runs.start("u2", "s1", 1, held_stream(release, ["x"]), lambda r, c: None)
        release.set()
        await asyncio.wait_for(other.task, WAIT_SECONDS)

    asyncio.run(scenario())


def test_a_reply_that_failed_or_said_nothing_is_not_saved():
    assert finished_reply([line({"delta": "half"}), line({"error": "boom"})], MODEL) is None
    assert finished_reply([line({"done": True})], MODEL) is None
    saved = finished_reply([line({"model_choice": {"model": "coder:7b", "role": "code"}}),
                            line({"untrusted_read": True}), line({"delta": "ok"})], "auto")
    assert (saved["model"], saved["auto"], saved["untrustedRead"], saved["content"]) == ("coder:7b", "code", True, "ok")


async def one_part(text):
    yield line({"delta": text})
    yield line({"done": True})


def test_finished_replies_beyond_the_kept_byte_cap_are_dropped_oldest_first():
    part = "x" * 1000
    reply_bytes = len(line({"delta": part})) + len(line({"done": True}))

    async def scenario():
        runs = ReplyRuns(max_kept_bytes=2 * reply_bytes)
        finished = []
        for index in range(4):
            run = runs.start("u1", f"s{index}", 1, one_part(part), lambda r, c: None)
            await asyncio.wait_for(run.task, WAIT_SECONDS)
            finished.append(run.id)
        return [runs.get("u1", run_id) is not None for run_id in finished]

    assert asyncio.run(scenario()) == [False, False, True, True]


def test_a_running_reply_is_never_dropped_for_the_kept_byte_cap():
    async def scenario():
        runs = ReplyRuns(max_kept_bytes=1)
        release = asyncio.Event()
        running = runs.start("u1", "s1", 1, held_stream(release, ["a long first part", "!"]), lambda r, c: None)
        await asyncio.sleep(0)
        done = runs.start("u2", "s2", 1, one_part("short"), lambda r, c: None)
        await asyncio.wait_for(done.task, WAIT_SECONDS)
        kept = runs.get("u1", running.id) is not None
        release.set()
        await asyncio.wait_for(running.task, WAIT_SECONDS)
        return kept, runs.get("u2", done.id)

    kept, dropped = asyncio.run(scenario())
    assert kept and dropped is None


def test_a_reply_past_the_size_limit_is_stopped_with_an_error_and_reported_unfinished():
    async def two_parts():
        yield line({"delta": "x" * 10})
        yield line({"delta": "y" * 10})

    async def scenario():
        runs = ReplyRuns(max_reply_bytes=len(line({"delta": "x" * 10})) + 1)
        finished = []
        run = runs.start("u1", "s1", 1, two_parts(), lambda r, completed: finished.append(completed))
        await asyncio.wait_for(run.task, WAIT_SECONDS)
        return finished, [json.loads(chunk.decode()[len("data:"):]) for chunk in run.chunks]

    finished, events = asyncio.run(scenario())
    assert finished == [False]
    assert events == [{"delta": "x" * 10}, {"error": "this reply grew too long to keep, so it was stopped"}]


def test_a_finished_reply_is_forgotten_after_its_keeping_time():
    now = [100.0]

    async def scenario():
        runs = ReplyRuns(clock=lambda: now[0], keep_finished_seconds=10)
        run = runs.start("u1", "s1", 1, one_part("done"), lambda r, c: None)
        await asyncio.wait_for(run.task, WAIT_SECONDS)
        now[0] += 9
        kept = runs.get("u1", run.id) is not None
        now[0] += 2
        return kept, runs.get("u1", run.id)

    kept, later = asyncio.run(scenario())
    assert kept and later is None


def test_a_reply_is_only_placed_right_after_the_question_it_answers(tmp_path):
    store = ChatSessionStore(str(tmp_path))
    answered = store.add(messages=[{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}])
    asked = store.add(messages=[{"role": "user", "content": "q"}])
    reply = {"role": "assistant", "content": "late"}
    assert store.place_reply(answered.id, 2, reply) is False
    assert store.place_reply(asked.id, 2, reply) is False
    assert store.place_reply(asked.id, 1, reply) is True
    assert [m["content"] for m in store.get(answered.id).messages] == ["q", "a"]
    assert [m["content"] for m in store.get(asked.id).messages] == ["q", "late"]
