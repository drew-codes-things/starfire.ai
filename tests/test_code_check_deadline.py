import asyncio
import json
import os
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_CONFIGS = os.path.join(ROOT, "server_configs")
sys.path.insert(0, SERVER_CONFIGS)

import code_check

yaml = pytest.importorskip("yaml")

MERGE_LEVELS = 14
MERGE_KEY_BOMB = "a: &a {k0: 0}\n" + "".join(
    f"m{level}: &m{level} {{<<: [{', '.join(['*' + ('a' if level == 0 else f'm{level - 1}')] * 8)}], k{level + 1}: 0}}\n"
    for level in range(MERGE_LEVELS))
SCENARIO_TIMEOUT_SECONDS = 60
SCENARIO = """
import asyncio, json, sys, time
sys.path.insert(0, sys.argv[1])
import code_check

async def main():
    started = time.monotonic()
    bombs = [asyncio.create_task(code_check.check("yaml", sys.argv[2], account=account)) for account in "ab"]
    results = await asyncio.gather(*bombs)
    after = await code_check.check("json", "{}", account="a")
    print(json.dumps({"bombs": [r["status"] for r in results], "after": after["status"],
                      "seconds": time.monotonic() - started}))

asyncio.run(main())
"""


def test_the_reviewers_merge_key_bomb_is_911_bytes():
    assert len(MERGE_KEY_BOMB) == 911


def test_the_merge_key_bomb_sent_twice_does_not_hold_the_check_slots():
    try:
        finished = subprocess.run([sys.executable, "-c", SCENARIO, SERVER_CONFIGS, MERGE_KEY_BOMB],
                                  capture_output=True, text=True, timeout=SCENARIO_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        pytest.fail(f"two YAML checks still held the slots after {SCENARIO_TIMEOUT_SECONDS}s")
    report = json.loads(finished.stdout.strip().splitlines()[-1])
    assert report["bombs"] == ["ok", "ok"] and report["after"] == "ok", report
    assert report["seconds"] < 10, report


def test_yaml_anchors_are_still_accepted_and_a_missing_one_is_an_error():
    assert code_check.check_sync("yaml", "base: &b {x: 1}\nother:\n  <<: *b\n  y: 2\n")["status"] == "ok"
    missing = code_check.check_sync("yaml", "a: *nowhere\n")
    assert missing["status"] == "error" and missing["line"] == 1


def test_a_check_that_runs_past_the_deadline_is_stopped_and_frees_its_slot(monkeypatch):
    slots = code_check.CheckSlots()
    monkeypatch.setattr(code_check, "check_slots", slots)
    monkeypatch.setattr(code_check, "CHECK_TIMEOUT_S", 1)
    monkeypatch.setattr(code_check, "parser_argv",
                        lambda language: [sys.executable, "-c", "import time; time.sleep(60)"], raising=False)

    async def two_slow_checks():
        return await asyncio.gather(code_check.check("yaml", "a: 1", account="a"),
                                    code_check.check("python", "x = 1", account="b"))

    started = time.monotonic()
    results = asyncio.run(two_slow_checks())
    assert time.monotonic() - started < 10
    assert [r["status"] for r in results] == ["skipped", "skipped"], results
    assert all("stopped" in r["message"] for r in results), results
    slots.take("a")
    slots.take("b")


@pytest.mark.parametrize("language", ["python", "json", "toml", "xml", "yaml"])
def test_parsers_do_not_start_without_prlimit(monkeypatch, language):
    monkeypatch.setattr(code_check.shutil, "which", lambda name: None if name == "prlimit" else f"/usr/bin/{name}")
    result = code_check.check_sync(language, "a")
    assert result["status"] == "skipped" and result["message"] == getattr(code_check, "NEEDS_LIMITER", None)


@pytest.mark.parametrize("language", ["python", "json", "toml", "xml", "yaml"])
def test_parsers_run_in_a_child_under_prlimit_memory_cpu_and_process_limits(monkeypatch, language):
    ran = []

    def run(argv, **kwargs):
        ran.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, json.dumps(code_check._result(language, "ok", "test")).encode(), b"")

    monkeypatch.setattr(code_check.shutil, "which", lambda name: "/usr/bin/prlimit" if name == "prlimit" else None)
    monkeypatch.setattr(code_check.subprocess, "run", run)
    assert code_check.check_sync(language, "a")["status"] == "ok"
    argv = ran[0]
    assert argv[0] == "/usr/bin/prlimit"
    flags = argv[1:argv.index("--")]
    limits = code_check.PARSER_LIMITS
    assert {f"--data={limits.memory_bytes}", f"--cpu={limits.cpu_seconds}", f"--nproc={limits.processes}"} <= set(flags)
    assert argv[argv.index("--") + 1:] == code_check.parser_argv(language)
