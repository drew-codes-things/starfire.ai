import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass

from host_sandbox import sandboxed_argv, scrubbed_env, working_sandbox_program
from syntax_parsers import PARSERS, first_int as _first_int, result as _result

CHECK_TIMEOUT_S = 20
MAX_CODE_CHARS = 200_000
MEBIBYTE = 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
LIMITER_PROGRAM = "prlimit"
NEEDS_LIMITER = "code checks need prlimit from util-linux, which is not installed on this server"
NEEDS_SANDBOX = ("C and C++ are only checked inside the bubblewrap sandbox, which is not installed or cannot "
                 "start on this server")


@dataclass(frozen=True)
class RunnerLimits:
    memory_bytes: int = 512 * MEBIBYTE
    cpu_seconds: int = 10
    file_bytes: int = 8 * MEBIBYTE
    processes: int = 32


RUNNER_LIMITS = RunnerLimits()
NODE_LIMITS = RunnerLimits(memory_bytes=1024 * MEBIBYTE)
PARSER_LIMITS = RunnerLimits(processes=1)
PARSER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "syntax_parsers.py")


class RunnerUnavailable(Exception):
    pass

LANGUAGE_ALIASES = {
    "python": "python", "py": "python", "python3": "python",
    "javascript": "javascript", "js": "javascript", "node": "javascript", "nodejs": "javascript",
    "mjs": "javascript", "cjs": "javascript",
    "typescript": "typescript", "ts": "typescript",
    "bash": "shell", "sh": "shell", "shell": "shell", "zsh": "shell",
    "json": "json",
    "toml": "toml",
    "xml": "xml", "svg": "xml",
    "go": "go", "golang": "go",
    "c": "c",
    "cpp": "cpp", "c++": "cpp", "cc": "cpp", "cxx": "cpp",
    "ruby": "ruby", "rb": "ruby",
    "php": "php",
    "yaml": "yaml", "yml": "yaml",
}

NODE_MODULE_HINTS = ("Cannot use import statement outside a module", "Unexpected token 'export'",
                     "Cannot use 'import.meta' outside a module")


def canonical_language(language: str) -> str | None:
    return LANGUAGE_ALIASES.get((language or "").strip().lower())


def limited_argv(limiter: str, argv: list[str], limits: RunnerLimits, count_processes: bool) -> list[str]:
    flags = [f"--data={limits.memory_bytes}", f"--cpu={limits.cpu_seconds}", f"--fsize={limits.file_bytes}",
             "--core=0"]
    if count_processes:
        flags.append(f"--nproc={limits.processes}")
    return [limiter, *flags, "--", *argv]


def runner_argv(argv: list[str], cwd: str, hidden_dirs: tuple[str, ...], limits: RunnerLimits,
                needs_sandbox: bool = False) -> list[str]:
    limiter = shutil.which(LIMITER_PROGRAM)
    if limiter is None:
        raise RunnerUnavailable(NEEDS_LIMITER)
    program = working_sandbox_program()
    if program is None:
        if needs_sandbox:
            raise RunnerUnavailable(NEEDS_SANDBOX)
        return limited_argv(limiter, argv, limits, count_processes=False)
    inner = limited_argv(limiter, argv, limits, count_processes=True)
    return sandboxed_argv(program, inner, cwd, cwd, hidden_dirs, scrubbed_env(), share_network=False)


def _run(cmd: list[str], cwd: str, hidden_dirs: tuple[str, ...] = (), limits: RunnerLimits = RUNNER_LIMITS,
         needs_sandbox: bool = False) -> tuple[int, str]:
    argv = runner_argv(cmd, cwd, hidden_dirs, limits, needs_sandbox)
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            proc = subprocess.run(argv, stdout=stdout_file, stderr=stderr_file, timeout=CHECK_TIMEOUT_S, cwd=cwd,
                                  stdin=subprocess.DEVNULL, env=scrubbed_env())
        except subprocess.TimeoutExpired:
            return 124, f"{cmd[0]} timed out after {CHECK_TIMEOUT_S}s"
        except OSError as e:
            return 127, str(e)
        stdout, stderr = _start_of(stdout_file), _start_of(stderr_file)
    output = stderr + ("\n" if stderr and stdout else "") + stdout
    return proc.returncode, output.strip()


def _start_of(output_file) -> str:
    output_file.seek(0)
    return output_file.read(MAX_OUTPUT_BYTES).decode("utf-8", errors="replace")


def parser_argv(language: str) -> list[str]:
    return [sys.executable, "-I", PARSER_SCRIPT, language]


def _check_in_parser_process(language: str, code: str) -> dict:
    limiter = shutil.which(LIMITER_PROGRAM)
    if limiter is None:
        raise RunnerUnavailable(NEEDS_LIMITER)
    argv = limited_argv(limiter, parser_argv(language), PARSER_LIMITS, count_processes=True)
    try:
        proc = subprocess.run(argv, input=code.encode("utf-8", errors="replace"), capture_output=True,
                              timeout=CHECK_TIMEOUT_S, env=scrubbed_env())
    except subprocess.TimeoutExpired:
        return _result(language, "skipped", "", f"the check took longer than {CHECK_TIMEOUT_S}s and was stopped")
    if proc.returncode != 0:
        return _result(language, "skipped", "", "the check stopped before it finished (it may be too large)")
    return json.loads(proc.stdout.decode("utf-8"))


def _write_temp(code: str, suffix: str, prefix: str = "") -> tuple[str, str]:
    directory = tempfile.mkdtemp(prefix="starfire-check-")
    path = os.path.join(directory, f"snippet{suffix}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(prefix + code)
        if not code.endswith("\n"):
            fh.write("\n")
    return directory, path


NOISE_PREFIXES = ("    at ", "Node.js v", "(Use `node")
LINE_PATTERN = r"(?:snippet(?:\.\w+)?:|line )(\d+)"


def _tidy(output: str, path: str, directory: str) -> str:
    cleaned = output.replace(path, "snippet").replace(directory + os.sep, "").replace(directory, "")
    lines = [line.rstrip() for line in cleaned.splitlines()
             if line.strip() and not line.startswith(NOISE_PREFIXES)]
    return "\n".join(lines[:12])


def _tool_check(language: str, checker: str, tool: str, suffix: str, code: str, argv, hidden_dirs: tuple[str, ...],
                prefix: str = "", retry=None, limits: RunnerLimits = RUNNER_LIMITS) -> dict:
    binary = shutil.which(tool)
    if not binary:
        return _result(language, "skipped", checker, f"{tool} is not installed")
    directory, path = _write_temp(code, suffix, prefix)
    try:
        rc, output = _run(argv(binary, path), directory, hidden_dirs, limits)
        if rc != 0 and retry:
            retried = retry(binary, directory, code, output)
            if retried is not None:
                rc, output, path = retried
        if rc == 0:
            return _result(language, "ok", checker)
        message = _tidy(output, path, directory)
        return _result(language, "error", checker, message, _first_int(LINE_PATTERN, message))
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _check_javascript(code: str, hidden_dirs: tuple[str, ...]) -> dict:
    def retry(binary, directory, code, output):
        if not any(hint in output for hint in NODE_MODULE_HINTS):
            return None
        path = os.path.join(directory, "snippet.mjs")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(code)
        rc, out = _run([binary, "--check", path], directory, hidden_dirs, NODE_LIMITS)
        return rc, out, path

    return _tool_check("javascript", "node --check", "node", ".js", code,
                       lambda b, p: [b, "--check", p], hidden_dirs, retry=retry, limits=NODE_LIMITS)


STRIP_TYPES_SCRIPT = (
    "const fs = require('node:fs');"
    "const { stripTypeScriptTypes } = require('node:module');"
    "if (typeof stripTypeScriptTypes !== 'function') { process.stderr.write('unsupported'); process.exit(3); }"
    "const [src, dest] = process.argv.slice(1);"
    "fs.writeFileSync(dest, stripTypeScriptTypes(fs.readFileSync(src, 'utf8'), { mode: 'strip' }));"
)


def _check_typescript(code: str, hidden_dirs: tuple[str, ...]) -> dict:
    binary = shutil.which("node")
    if not binary:
        return _result("typescript", "skipped", "node --check", "node is not installed")
    directory, path = _write_temp(code, ".ts")
    stripped = os.path.join(directory, "snippet.mjs")
    try:
        rc, output = _run([binary, "--no-warnings", "-e", STRIP_TYPES_SCRIPT, path, stripped], directory,
                          hidden_dirs, NODE_LIMITS)
        if rc == 3:
            return _result("typescript", "skipped", "node --check", "needs Node.js 22.13 or newer")
        if rc == 0:
            rc, output = _run([binary, "--check", stripped], directory, hidden_dirs, NODE_LIMITS)
        if rc == 0:
            return _result("typescript", "ok", "node --check")
        message = _tidy(output, path, directory).replace("snippet.mjs", "snippet")
        if message.startswith(":"):
            message = "snippet" + message
        return _result("typescript", "error", "node --check", message, _first_int(LINE_PATTERN, message))
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _check_shell(code: str, hidden_dirs: tuple[str, ...]) -> dict:
    return _tool_check("shell", "bash -n", "bash", ".sh", code, lambda b, p: [b, "-n", p], hidden_dirs)


def _check_go(code: str, hidden_dirs: tuple[str, ...]) -> dict:
    def retry(binary, directory, code, output):
        if "expected 'package'" not in output:
            return None
        path = os.path.join(directory, "snippet.go")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("package main\n\n" + code)
        rc, out = _run([binary, "-e", path], directory, hidden_dirs)
        return rc, out, path

    return _tool_check("go", "gofmt -e", "gofmt", ".go", code, lambda b, p: [b, "-e", p], hidden_dirs, retry=retry)


LINE_ENDS = re.compile(r"\r\n?")
LINE_SPLICE = re.compile(r"\\\n")
FILE_DIRECTIVE = re.compile(r"^[ \t\f\v]*(?:#|%:)[ \t\f\v]*(include_next|include|import|embed)\b[ \t\f\v]*(.*)$", re.M)
SYSTEM_HEADER = re.compile(r"<([A-Za-z0-9_][A-Za-z0-9_+./-]*)>")


def _is_system_header(directive: str, argument: str) -> bool:
    if directive != "include":
        return False
    match = SYSTEM_HEADER.fullmatch(argument.strip())
    return bool(match) and ".." not in match.group(1)


BLOCK_COMMENT_START, BLOCK_COMMENT_END, LINE_COMMENT_START = "/*", "*/", "//"


def _next_comment_start(text: str, position: int) -> int:
    slash = text.find("/", position)
    while slash >= 0 and not text.startswith((BLOCK_COMMENT_START, LINE_COMMENT_START), slash):
        slash = text.find("/", slash + 1)
    return slash


def _without_c_comments(text: str) -> str:
    pieces, position = [], 0
    while (start := _next_comment_start(text, position)) >= 0:
        pieces.append(text[position:start] + " ")
        if text.startswith(BLOCK_COMMENT_START, start):
            end = text.find(BLOCK_COMMENT_END, start + len(BLOCK_COMMENT_START))
            position = end + len(BLOCK_COMMENT_END) if end >= 0 else len(text)
        else:
            end = text.find("\n", start)
            position = end if end >= 0 else len(text)
    pieces.append(text[position:])
    return "".join(pieces)


def _names_files_outside_system_headers(text: str) -> bool:
    return any(not _is_system_header(m.group(1), m.group(2)) for m in FILE_DIRECTIVE.finditer(text))


def _reads_files_outside_system_headers(code: str) -> bool:
    spliced = LINE_SPLICE.sub("", LINE_ENDS.sub("\n", code))
    without_comments = _without_c_comments(spliced)
    return _names_files_outside_system_headers(spliced) or _names_files_outside_system_headers(without_comments)


MAX_COMPILER_ERRORS = 20
ERROR_LIMIT_FLAGS = {"gcc": f"-fmax-errors={MAX_COMPILER_ERRORS}", "clang": f"-ferror-limit={MAX_COMPILER_ERRORS}"}


def _compiler_argv(tool: str, path: str) -> list[str]:
    error_limit = [ERROR_LIMIT_FLAGS[tool]] if tool in ERROR_LIMIT_FLAGS else []
    return [shutil.which(tool), "-fsyntax-only", "-w", *error_limit, path]


def _c_compiler() -> str | None:
    for tool in ("gcc", "clang", "cc"):
        if shutil.which(tool):
            return tool
    return None


INCLUDED_FILE_NOTE = "(messages about other files are not shown)"
QUOTED_SOURCE = re.compile(r"^\s|^\s*\|")


def _snippet_messages_only(message: str) -> str:
    kept, keeping, hidden = [], False, False
    for line in message.splitlines():
        if not QUOTED_SOURCE.match(line):
            keeping = line.startswith("snippet:")
        if keeping:
            kept.append(line)
        else:
            hidden = True
    if hidden:
        kept.append(INCLUDED_FILE_NOTE)
    return "\n".join(kept)


def _check_c_family(language: str, code: str, hidden_dirs: tuple[str, ...] = ()) -> dict:
    tool = _c_compiler()
    if not tool:
        return _result(language, "skipped", "cc -fsyntax-only", "no C compiler installed")
    checker = f"{tool} -fsyntax-only"
    if _reads_files_outside_system_headers(code):
        return _result(language, "skipped", checker,
                       "only code whose #include lines name system headers like <stdio.h> is checked")
    if working_sandbox_program() is None:
        return _result(language, "skipped", checker, NEEDS_SANDBOX)
    directory, path = _write_temp(code, ".c" if language == "c" else ".cpp")
    try:
        rc, output = _run(_compiler_argv(tool, path), directory, hidden_dirs, needs_sandbox=True)
        if rc == 0:
            return _result(language, "ok", checker)
        message = _snippet_messages_only(_tidy(output, path, directory))
        return _result(language, "error", checker, message, _first_int(LINE_PATTERN, message))
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _check_ruby(code: str, hidden_dirs: tuple[str, ...]) -> dict:
    return _tool_check("ruby", "ruby -c", "ruby", ".rb", code, lambda b, p: [b, "-c", p], hidden_dirs)


def _check_php(code: str, hidden_dirs: tuple[str, ...]) -> dict:
    prefix = "" if code.lstrip().startswith("<?") else "<?php\n"
    return _tool_check("php", "php -l", "php", ".php", code, lambda b, p: [b, "-l", p], hidden_dirs, prefix=prefix)


RUNNER_CHECKERS = {
    "javascript": _check_javascript,
    "typescript": _check_typescript,
    "shell": _check_shell,
    "go": _check_go,
    "c": lambda code, hidden_dirs: _check_c_family("c", code, hidden_dirs),
    "cpp": lambda code, hidden_dirs: _check_c_family("cpp", code, hidden_dirs),
    "ruby": _check_ruby,
    "php": _check_php,
}


def check_sync(language: str, code: str, hidden_dirs: tuple[str, ...] = ()) -> dict:
    canonical = canonical_language(language)
    if not canonical:
        return _result((language or "").strip().lower(), "skipped", "", "no checker for this language")
    if not code.strip():
        return _result(canonical, "skipped", "", "empty code block")
    if len(code) > MAX_CODE_CHARS:
        return _result(canonical, "skipped", "", "code block too large to check")
    if "\x00" in code:
        return _result(canonical, "error", "", "code contains a null byte")
    try:
        if canonical in PARSERS:
            return _check_in_parser_process(canonical, code)
        return RUNNER_CHECKERS[canonical](code, hidden_dirs)
    except RunnerUnavailable as e:
        return _result(canonical, "skipped", "", str(e))
    except Exception as e:
        return _result(canonical, "skipped", "", f"checker failed: {e}")


MAX_CHECKS_AT_ONCE = 2
MAX_CHECKS_PER_ACCOUNT = 1
CHECKS_BUSY = "the code checker is busy - try again in a moment"
ACCOUNT_CHECK_RUNNING = "this account already has a code check running - try again when it finishes"


class ChecksBusy(Exception):
    pass


class CheckSlots:

    def __init__(self, size: int = MAX_CHECKS_AT_ONCE, per_account: int = MAX_CHECKS_PER_ACCOUNT):
        self.size = size
        self.per_account = per_account
        self._lock = threading.Lock()
        self._running: dict[str, int] = {}

    def take(self, account: str) -> None:
        with self._lock:
            if self._running.get(account, 0) >= self.per_account:
                raise ChecksBusy(ACCOUNT_CHECK_RUNNING)
            if sum(self._running.values()) >= self.size:
                raise ChecksBusy(CHECKS_BUSY)
            self._running[account] = self._running.get(account, 0) + 1

    def give_back(self, account: str) -> None:
        with self._lock:
            self._running[account] -= 1
            if self._running[account] <= 0:
                del self._running[account]


check_slots = CheckSlots()


def _check_in_slot(language: str, code: str, hidden_dirs: tuple[str, ...], account: str) -> dict:
    try:
        return check_sync(language, code, hidden_dirs)
    finally:
        check_slots.give_back(account)


async def check(language: str, code: str, hidden_dirs: tuple[str, ...] = (), *, account: str) -> dict:
    try:
        check_slots.take(account)
    except ChecksBusy as busy:
        return _result(canonical_language(language) or (language or "").strip().lower(), "skipped", "", str(busy))
    return await asyncio.to_thread(_check_in_slot, language, code, hidden_dirs, account)
