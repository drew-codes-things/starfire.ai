from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

SAFE_ENV_NAMES = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM", "SHELL")
GITHUB_ENV_NAMES = ("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST", "GH_CONFIG_DIR")
SANDBOX_PROGRAM = "bwrap"
SHELL = "/bin/sh"
READ_CHUNK_BYTES = 4096
SANDBOX_PROBE_TIMEOUT_SECONDS = 10
SANDBOX_RETRY_SECONDS = 300
SYSTEM_FOLDERS = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32", "/etc/alternatives")
SYSTEM_FILES = ("/etc/ld.so.cache",)
ISOLATION_FLAGS = ("--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup-try",
                   "--die-with-parent", "--new-session", "--clearenv")


@dataclass(frozen=True)
class HostLimits:
    workspace: str = ""
    hidden_dirs: tuple[str, ...] = ()
    edit_roots: tuple[str, ...] = ()
    protected: tuple[str, ...] = ()


@dataclass
class CappedRun:
    returncode: int | None
    output: bytes
    truncated: bool
    timed_out: bool


def scrubbed_env(extra_names: tuple[str, ...] = ()) -> dict[str, str]:
    return {name: os.environ[name] for name in (*SAFE_ENV_NAMES, *extra_names) if name in os.environ}


def sandbox_program() -> str | None:
    return shutil.which(SANDBOX_PROGRAM)


def _probe_sandbox(program: str) -> bool:
    probe = [program, "--ro-bind", "/", "/", "--unshare-user", "--unshare-pid", "--unshare-net", "--die-with-parent",
             "--", SHELL, "-c", "exit 0"]
    try:
        finished = subprocess.run(probe, stdin=subprocess.DEVNULL, capture_output=True,
                                  timeout=SANDBOX_PROBE_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("the %s sandbox could not be started: %s", program, e)
        return False
    if finished.returncode != 0:
        logger.warning("the %s sandbox could not be started: %s", program, finished.stderr.decode(errors="replace"))
    return finished.returncode == 0


_probe_lock = threading.Lock()
_programs_that_start: set[str] = set()
_failed_probe_at: dict[str, float] = {}


def _failed_recently(program: str) -> bool:
    failed_at = _failed_probe_at.get(program)
    return failed_at is not None and time.monotonic() - failed_at < SANDBOX_RETRY_SECONDS


def sandbox_starts(program: str) -> bool:
    with _probe_lock:
        if program in _programs_that_start:
            return True
        if _failed_recently(program):
            return False
        starts = _probe_sandbox(program)
        if starts:
            _programs_that_start.add(program)
            _failed_probe_at.pop(program, None)
        else:
            _failed_probe_at[program] = time.monotonic()
        return starts


def working_sandbox_program() -> str | None:
    program = sandbox_program()
    return program if program and sandbox_starts(program) else None


def _existing(paths) -> list[str]:
    return [path for path in dict.fromkeys(os.path.abspath(p) for p in paths if p) if os.path.isdir(path)]


def _isolated(wrapped: list[str], argv: list[str], cwd: str, env: dict[str, str], share_network: bool) -> list[str]:
    wrapped = [*wrapped, *ISOLATION_FLAGS]
    if not share_network:
        wrapped.append("--unshare-net")
    for name, value in env.items():
        wrapped += ["--setenv", name, value]
    return wrapped + ["--chdir", cwd, "--", *argv]


def sandboxed_argv(program: str, argv: list[str], cwd: str, writable: str, hidden_dirs, env: dict[str, str],
                   share_network: bool = True) -> list[str]:
    wrapped = [program, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
    for hidden in _existing(hidden_dirs):
        wrapped += ["--tmpfs", hidden]
    if writable:
        wrapped += ["--bind", writable, writable]
    return _isolated(wrapped, argv, cwd, env, share_network)


def _system_folder_binds() -> list[str]:
    binds = []
    for folder in SYSTEM_FOLDERS:
        if os.path.islink(folder):
            binds += ["--symlink", os.readlink(folder), folder]
        elif os.path.isdir(folder):
            binds += ["--ro-bind", folder, folder]
    for file in SYSTEM_FILES:
        if os.path.isfile(file):
            binds += ["--ro-bind", file, file]
    return binds


def system_folders_argv(program: str, argv: list[str], cwd: str, env: dict[str, str]) -> list[str]:
    wrapped = [program, *_system_folder_binds(), "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
               "--bind", cwd, cwd]
    return _isolated(wrapped, argv, cwd, env, share_network=False)


def sandboxed_shell_argv(program: str, command: str, cwd: str, limits: HostLimits, env: dict[str, str]) -> list[str]:
    return sandboxed_argv(program, [SHELL, "-c", command], cwd, limits.workspace, limits.hidden_dirs, env)


def shell_argv(command: str, cwd: str, limits: HostLimits, env: dict[str, str]) -> tuple[list[str], str | None]:
    program = working_sandbox_program()
    if program is None:
        logger.warning("run_shell is running without a sandbox because %s is not installed or cannot start",
                       SANDBOX_PROGRAM)
        return [SHELL, "-c", command], cwd
    return sandboxed_shell_argv(program, command, cwd, limits, env), None


def _stop_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        logger.debug("process group %s had already ended", proc.pid)


async def _read_capped(stream: asyncio.StreamReader, max_bytes: int) -> tuple[bytes, bool]:
    collected = bytearray()
    while chunk := await stream.read(READ_CHUNK_BYTES):
        collected += chunk
        if len(collected) > max_bytes:
            return bytes(collected[:max_bytes]), True
    return bytes(collected), False


async def _discard_rest(stream: asyncio.StreamReader) -> None:
    while await stream.read(READ_CHUNK_BYTES):
        continue


async def run_capped(argv: list[str], cwd: str | None, env: dict[str, str], timeout_seconds: float,
                     max_bytes: int) -> CappedRun:
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=cwd or None, env=env, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True,
    )
    try:
        output, truncated = await asyncio.wait_for(_read_capped(proc.stdout, max_bytes), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        _stop_group(proc)
        await _discard_rest(proc.stdout)
        await proc.wait()
        return CappedRun(None, b"", False, True)
    if truncated:
        _stop_group(proc)
        await _discard_rest(proc.stdout)
    returncode = await proc.wait()
    return CappedRun(returncode, output, truncated, False)
