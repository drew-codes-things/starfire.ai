from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import code_check
import host_sandbox

needs_cc = pytest.mark.skipif(code_check._c_compiler() is None, reason="no C compiler installed")
needs_limiter = pytest.mark.skipif(shutil.which("prlimit") is None, reason="prlimit is not installed here")
needs_sandbox = pytest.mark.skipif(host_sandbox.working_sandbox_program() is None,
                                   reason="bubblewrap is not installed or cannot start here")

RUNNER_LANGUAGES = {
    "javascript": "const a = 1;\n",
    "typescript": "const a: number = 1;\n",
    "shell": "echo hi\n",
    "go": "package main\n",
    "c": "int main(void) { return 0; }\n",
    "cpp": "int main() { return 0; }\n",
    "ruby": "puts 1\n",
    "php": "<?php echo 1;\n",
}
FAKE_SANDBOX = "/usr/bin/bwrap"
LIMIT_FLAGS = ("--data=", "--cpu=", "--fsize=")
DEV_ZERO_INCLUDE = '#/**/include "/dev/zero"\nint main(void) { return 0; }\n'


def fake_tools(monkeypatch, sandbox: str | None, limiter: str | None = "/usr/bin/prlimit") -> list[list[str]]:
    ran = []

    def which(name):
        if name == "prlimit":
            return limiter
        return f"/usr/bin/{name}"

    def run(argv, **kwargs):
        ran.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(code_check.shutil, "which", which)
    monkeypatch.setattr(code_check.subprocess, "run", run)
    monkeypatch.setattr(code_check, "working_sandbox_program", lambda: sandbox, raising=False)
    monkeypatch.setattr(code_check, "sandbox_program", lambda: sandbox, raising=False)
    return ran


def limiter_flags(argv: list[str]) -> list[str]:
    start = argv.index("/usr/bin/prlimit")
    return argv[start + 1:argv.index("--", start)]


@pytest.mark.parametrize("language", sorted(RUNNER_LANGUAGES))
def test_every_runner_starts_inside_the_sandbox_under_memory_cpu_file_and_process_limits(monkeypatch, language):
    ran = fake_tools(monkeypatch, FAKE_SANDBOX)
    code_check.check_sync(language, RUNNER_LANGUAGES[language], ("/srv/data",))
    assert ran, language
    for argv in ran:
        assert argv[0] == FAKE_SANDBOX and "--unshare-net" in argv
        assert "/usr/bin/prlimit" in argv[argv.index("--"):], argv
        flags = limiter_flags(argv)
        assert all(any(flag.startswith(name) for flag in flags) for name in (*LIMIT_FLAGS, "--nproc=")), flags


@pytest.mark.parametrize("language", sorted(set(RUNNER_LANGUAGES) - {"c", "cpp"}))
def test_without_the_sandbox_runners_still_get_memory_cpu_and_file_limits(monkeypatch, language):
    ran = fake_tools(monkeypatch, None)
    code_check.check_sync(language, RUNNER_LANGUAGES[language])
    assert ran, language
    for argv in ran:
        assert argv[0] == "/usr/bin/prlimit", argv
        flags = limiter_flags(argv)
        assert all(any(flag.startswith(name) for flag in flags) for name in LIMIT_FLAGS), flags


def test_without_prlimit_no_runner_starts(monkeypatch):
    ran = fake_tools(monkeypatch, FAKE_SANDBOX, limiter=None)
    for language, code in RUNNER_LANGUAGES.items():
        result = code_check.check_sync(language, code)
        assert result["status"] == "skipped" and result["message"] == code_check.NEEDS_LIMITER, result
    assert ran == []


@needs_cc
def test_an_include_hidden_behind_a_comment_is_refused_before_compiling(monkeypatch):
    ran = fake_tools(monkeypatch, FAKE_SANDBOX)
    monkeypatch.setattr(code_check, "_c_compiler", lambda: "gcc")
    for trick in (DEV_ZERO_INCLUDE, '#include /* x */ "/dev/zero"\n', '#\\\n/**/include "/dev/zero"\n'):
        result = code_check.check_sync("c", trick)
        assert result["status"] == "skipped" and "system headers" in result["message"], trick
    assert ran == []


SECONDS_FOR_THE_COMMENT_FILTER = 0.5
COMMENT_OPENER_LINES = {
    "the review's 20,000 lines": "/*\n" * 20_000,
    "the longest snippet a check takes": "/*\n" * (code_check.MAX_CODE_CHARS // len("/*\n")),
}


@pytest.mark.parametrize("label", COMMENT_OPENER_LINES)
def test_the_comment_filter_takes_time_in_step_with_the_snippet_length(label):
    started = time.perf_counter()
    assert code_check._reads_files_outside_system_headers(COMMENT_OPENER_LINES[label]) is False
    assert time.perf_counter() - started < SECONDS_FOR_THE_COMMENT_FILTER


@pytest.mark.parametrize("code, without_comments", [
    ("a /* b */ c // d\ne", "a   c  \ne"),
    ("x /* never closed\n#include \"y\"\n", "x  "),
    ("// a /* b\nc */ d", " \nc */ d"),
    ("/*/ still open */ z", "  z"),
    ("a / b * c", "a / b * c"),
    ("p = q /// r\n", "p = q  \n"),
])
def test_comments_are_removed_the_way_the_compiler_reads_them(code, without_comments):
    assert code_check._without_c_comments(code) == without_comments


def test_an_include_after_a_closed_comment_on_its_line_is_still_found():
    assert code_check._reads_files_outside_system_headers('/* a\n b */ #include "/etc/hostname"\n')
    assert not code_check._reads_files_outside_system_headers('/* #include "/etc/hostname" */\n')


@needs_cc
@pytest.mark.parametrize("trick", [
    '#\finclude "/dev/zero"\n',
    '\v#include "/dev/zero"\n',
    'int a;\r#include "/dev/zero"\rint main(void) { return 0; }\r',
    '#\\\rinclude "/dev/zero"\n',
    '\f%:\vinclude "/dev/zero"\n',
])
def test_an_include_after_a_form_feed_vertical_tab_or_bare_carriage_return_is_refused(monkeypatch, trick):
    ran = fake_tools(monkeypatch, FAKE_SANDBOX)
    monkeypatch.setattr(code_check, "_c_compiler", lambda: "gcc")
    result = code_check.check_sync("c", trick)
    assert result["status"] == "skipped" and "system headers" in result["message"], (trick, result)
    assert ran == []


def sandboxed_shell(script: str, directory: str, limits: code_check.RunnerLimits) -> subprocess.CompletedProcess:
    argv = code_check.runner_argv(["/bin/sh", "-c", script], directory, (), limits)
    return subprocess.run(argv, capture_output=True, text=True, timeout=60, cwd=directory)


@needs_limiter
@needs_sandbox
def test_the_sandboxed_runner_cannot_start_more_than_its_process_limit(tmp_path):
    limits = code_check.RunnerLimits(processes=8)
    finished = sandboxed_shell("i=0; while [ $i -lt 40 ]; do sleep 3 & i=$((i+1)); done; wait; echo all-started",
                               str(tmp_path), limits)
    assert "all-started" not in finished.stdout
    assert "fork" in (finished.stdout + finished.stderr).lower()


@needs_limiter
@needs_sandbox
def test_the_sandboxed_runner_cannot_write_past_its_file_limit(tmp_path):
    limits = code_check.RunnerLimits(file_bytes=1024 * 1024)
    finished = sandboxed_shell("head -c 20000000 /dev/zero > big", str(tmp_path), limits)
    assert finished.returncode != 0
    assert (tmp_path / "big").stat().st_size <= limits.file_bytes


@needs_limiter
@needs_sandbox
def test_the_sandboxed_runner_is_stopped_at_its_cpu_limit(tmp_path):
    limits = code_check.RunnerLimits(cpu_seconds=1)
    started = time.monotonic()
    finished = sandboxed_shell("while :; do :; done", str(tmp_path), limits)
    assert finished.returncode != 0
    assert time.monotonic() - started < 10


MEMORY_PROBE = """
import json, resource, sys, time
sys.path.insert(0, sys.argv[1])
import code_check
code_check._reads_files_outside_system_headers = lambda code: False
started = time.monotonic()
result = code_check.check_sync("c", sys.argv[2])
print(json.dumps({"result": result, "seconds": time.monotonic() - started,
                  "peak_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss}))
"""


@needs_cc
@needs_limiter
@needs_sandbox
def test_a_dev_zero_include_that_slips_past_the_filter_stays_under_the_memory_cap():
    finished = subprocess.run([sys.executable, "-c", MEMORY_PROBE, os.path.join(ROOT, "server_configs"),
                               DEV_ZERO_INCLUDE], capture_output=True, text=True, timeout=120)
    report = json.loads(finished.stdout.strip().splitlines()[-1])
    assert report["result"]["status"] == "error", report
    assert report["peak_kib"] * 1024 <= code_check.RUNNER_LIMITS.memory_bytes, report
    assert report["seconds"] < code_check.CHECK_TIMEOUT_S, report


@pytest.mark.parametrize("compiler, error_limit", [("gcc", "-fmax-errors=20"), ("clang", "-ferror-limit=20")])
def test_the_c_compiler_stops_after_twenty_errors(monkeypatch, compiler, error_limit):
    ran = fake_tools(monkeypatch, FAKE_SANDBOX)
    monkeypatch.setattr(code_check, "_c_compiler", lambda: compiler)
    code_check.check_sync("c", RUNNER_LANGUAGES["c"])
    assert len(ran) == 1 and error_limit in ran[0], ran


SERVER_MEMORY_PROBE = """
import json, sys, tempfile, time
sys.path.insert(0, sys.argv[1])
import code_check
started = time.monotonic()
if sys.argv[2] == "flood":
    with tempfile.TemporaryDirectory() as directory:
        rc, output = code_check._run(["/bin/sh", "-c", sys.argv[3]], directory)
    outcome = {"rc": rc, "output_chars": len(output)}
else:
    outcome = code_check.check_sync("c", sys.argv[3])
with open("/proc/self/status") as status:
    peak_kib = next(int(line.split()[1]) for line in status if line.startswith("VmHWM:"))
print(json.dumps({"outcome": outcome, "seconds": time.monotonic() - started, "peak_kib": peak_kib}))
"""
SERVER_PEAK_LIMIT_BYTES = 120 * 1024 * 1024
HUNDRED_MB_OF_ERRORS = "head -c 100000000 /dev/zero | tr '\\0' x >&2; exit 1"
MACRO_LEVELS = 7
MACRO_FLOOD = "#define E0 int 1x;\n" + "".join(
    f"#define E{level} " + " ".join([f"E{level - 1}"] * 10) + "\n" for level in range(1, MACRO_LEVELS + 1)
) + f"E{MACRO_LEVELS}\n"


def server_side_peak(kind: str, payload: str) -> dict:
    finished = subprocess.run([sys.executable, "-c", SERVER_MEMORY_PROBE, os.path.join(ROOT, "server_configs"),
                               kind, payload], capture_output=True, text=True, timeout=120)
    return json.loads(finished.stdout.strip().splitlines()[-1])


@needs_limiter
def test_a_runner_that_floods_its_output_costs_the_server_a_bounded_read():
    report = server_side_peak("flood", HUNDRED_MB_OF_ERRORS)
    assert report["outcome"]["rc"] != 0, report
    assert report["outcome"]["output_chars"] <= 2 * code_check.MAX_OUTPUT_BYTES, report
    assert report["peak_kib"] * 1024 < SERVER_PEAK_LIMIT_BYTES, report


@needs_cc
@needs_limiter
@needs_sandbox
def test_the_reviewers_macro_flood_ends_quickly_without_raising_server_memory():
    assert len(MACRO_FLOOD) == 309
    report = server_side_peak("check", MACRO_FLOOD)
    assert report["outcome"]["status"] == "error", report
    assert report["seconds"] < code_check.CHECK_TIMEOUT_S / 2, report
    assert report["peak_kib"] * 1024 < SERVER_PEAK_LIMIT_BYTES, report


def test_a_bubblewrap_that_cannot_start_counts_as_no_sandbox(tmp_path, monkeypatch):
    broken = tmp_path / "bwrap"
    broken.write_text("#!/bin/sh\necho 'bwrap: setting up uid map: Permission denied' >&2\nexit 1\n")
    broken.chmod(0o755)
    monkeypatch.setattr(host_sandbox, "sandbox_program", lambda: str(broken))
    assert host_sandbox.working_sandbox_program() is None
    result = code_check.check_sync("c", "int main(void) { return 0; }\n")
    assert result["status"] == "skipped"
