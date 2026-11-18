from __future__ import annotations

import shutil

from host_sandbox import GITHUB_ENV_NAMES, run_capped, scrubbed_env
from shell_tool import MAX_OUTPUT_BYTES, format_output

TIMEOUT_SECONDS = 60
READ_ONLY_SUBCOMMANDS = {
    "issue": {"list", "view", "status"},
    "pr": {"list", "view", "status", "diff", "checks"},
    "repo": {"list", "view"},
    "release": {"list", "view"},
    "run": {"list", "view"},
    "workflow": {"list", "view"},
    "label": {"list"},
    "gist": {"list", "view"},
    "auth": {"status"},
}
READ_ONLY_COMMANDS = frozenset({"search", "status"})
COMMANDS_THAT_ASK_FIRST = frozenset({"api", "org", "project", "ruleset", "cache", "browse"})
ARGS_REQUIRED = "error: args is required, e.g. [\"issue\", \"list\"]"


SAFE_READ_FLAGS = frozenset({
    "--json", "--limit", "-L", "--state", "-s", "--repo", "-R", "--label", "-l", "--author", "-A",
    "--assignee", "-a", "--search", "-S", "--comments", "-c", "--log", "--log-failed", "--branch", "-b",
    "--base", "-B", "--head", "-H", "--milestone", "-m", "--mention", "--app", "--draft", "-d", "--status",
    "--event", "-e", "--user", "-u", "--workflow", "--exit-status", "--attempt", "--name-only", "--patch",
    "--visibility", "--archived", "--no-archived", "--fork", "--source", "--language", "--topic", "--order",
    "--sort", "--owner", "--filename", "--raw", "--files", "--public", "--secret", "--required", "--commit",
    "--created", "--job", "-j", "--verbose", "-v", "--all", "--active", "--exclude-drafts",
    "--exclude-pre-releases", "--hostname",
})
CREDENTIAL_FLAGS = ("--show-token", "--jq", "--template")
CREDENTIAL_SHORT_FLAGS = frozenset("qt")
SIGN_IN_READ_FLAGS = frozenset({"--active", "-a", "--hostname"})
REFUSED_FLAG = ("error: github_cli does not pass --show-token, --jq, --template or their short forms (-t, -q), "
                "because they can print the GitHub token")


def _command(args: list[str]) -> tuple[str, str]:
    return (args[0] if args else ""), (args[1] if len(args) > 1 else "")


def _flag_name(arg: str) -> str:
    return arg.split("=", 1)[0]


def _is_flag(arg: str) -> bool:
    return arg.startswith("-") and arg != "-"


def _names_a_credential_flag(arg: str, short_flags_can_print: bool) -> bool:
    if any(_flag_name(arg) == flag for flag in CREDENTIAL_FLAGS):
        return True
    if arg.startswith("--") or not _is_flag(arg):
        return False
    letters = _flag_name(arg)[1:]
    return "q" in letters or (short_flags_can_print and bool(CREDENTIAL_SHORT_FLAGS & set(letters)))


def _names_only_read_commands(args: list[str]) -> bool:
    command, subcommand = _command(args)
    return command in READ_ONLY_COMMANDS or subcommand in READ_ONLY_SUBCOMMANDS.get(command, ())


def _flags(args: list[str]) -> list[str]:
    return [_flag_name(arg) for arg in args if _is_flag(arg)]


def is_read_only(args: list[str]) -> bool:
    return _names_only_read_commands(args) and all(flag in SAFE_READ_FLAGS for flag in _flags(args))


def _is_plain_sign_in_status(args: list[str]) -> bool:
    return args[:2] == ["auth", "status"] and all(flag in SIGN_IN_READ_FLAGS for flag in _flags(args))


def refusal(args) -> str | None:
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        return "error: args must be a list of strings"
    if not args:
        return ARGS_REQUIRED
    command, _ = _command(args)
    is_known = command in READ_ONLY_SUBCOMMANDS or command in READ_ONLY_COMMANDS or command in COMMANDS_THAT_ASK_FIRST
    if not is_known or (command == "auth" and not _is_plain_sign_in_status(args)):
        return (f"error: github_cli does not run 'gh {command}'; it runs issue, pr, repo, release, run, workflow, "
                "label, gist, search, status, api, org, project, ruleset, cache and 'auth status'")
    short_flags_can_print = command == "auth" or _names_only_read_commands(args) or command == "api"
    if any(_names_a_credential_flag(arg, short_flags_can_print) for arg in args):
        return REFUSED_FLAG
    return None


async def run_gh(args: list[str]) -> str:
    refused = refusal(args)
    if refused:
        return refused
    if shutil.which("gh") is None:
        return "error: the 'gh' CLI is not installed on this machine"

    try:
        run = await run_capped(["gh", *args], None, scrubbed_env(GITHUB_ENV_NAMES), TIMEOUT_SECONDS,
                               MAX_OUTPUT_BYTES)
    except OSError as e:
        return f"error: failed to start gh: {e}"
    if run.timed_out:
        return f"error: gh command timed out after {TIMEOUT_SECONDS}s"
    return format_output(run)
