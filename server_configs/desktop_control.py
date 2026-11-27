from __future__ import annotations

import asyncio
import html
import math
import re
import time
import uuid
from dataclasses import dataclass

DESKTOP_STEP_TIMEOUT_SECONDS = 200
STEP_ANSWER_MARGIN_SECONDS = 40
STEP_TIMEOUT_SECONDS = DESKTOP_STEP_TIMEOUT_SECONDS + STEP_ANSWER_MARGIN_SECONDS
NO_ANSWER = "no_answer"
MAX_CONTROL_STEPS_PER_TURN = 8
CONTROL_TOOL_ROUNDS = MAX_CONTROL_STEPS_PER_TURN + 4
MAX_RESULT_CHARS = 4000
MAX_DETAIL_CHARS = 300
MAX_APP_CHARS = 80
MAX_TARGET_CHARS = 80
MAX_TYPED_CHARS = 500
MAX_KEYS_CHARS = 40
MAX_SCREEN_PIXEL = 100_000
MAX_SCROLL_AMOUNT = 50
SCREENSHOT = "screenshot"
TEXT = "text"
NUMBER = "number"
WHOLE_NUMBER = "whole number"
YES_OR_NO = "true or false"
CLICK_BUTTONS = ("left", "right")
TYPED_TEXT = "typed text"
APP_NAME = "app name"
PATH_CHARACTERS = frozenset("/\\~:")
HIDDEN_FILE_START = "."
ARTY_CODE_OR_MARKUP = re.compile(r'["`<>{}()]')
ARTY_TYPED_LITERALLY = re.compile(r"[A-Za-z]:\\|\\\\|^\s*(?:echo|printf)\b")
SCROLL_DIRECTIONS = ("up", "down", "left", "right")
REASON_TEXTS = {
    "declined": "the user said no in Arty Desktop",
    "timed_out": "nobody allowed it in Arty Desktop in time",
    "abandoned": "Arty Desktop dropped the step before anyone answered it",
    "busy": "Arty Desktop was already waiting on another step",
    "bad_request": "Arty Desktop refused the step as it was written",
    "unavailable": "controlling the PC is turned off in Arty Desktop",
    "not_running": "Arty Desktop is not running on their computer",
    "not_linked": "starfire Desktop is not linked to Arty Desktop",
    "failed": "starfire Desktop reported a problem with the step",
    NO_ANSWER: "starfire Desktop never said how the step went",
}
REFUSED_BEFORE_RUNNING = frozenset({"declined", "timed_out", "abandoned", "busy", "bad_request", "unavailable",
                                    "not_running", "not_linked"})
DEFAULT_REASON_TEXT = "it failed for a reason starfire Desktop could not name"
REMOTE_MODEL = "remote_model"
CLOUD_NAME_ENDING = "-cloud"
CLOUD_NAME_TAG = ":cloud"
MODEL_TOO_SMALL = "model_too_small"
UNOFFERED_REASONS = {
    "not_linked": ("starfire Desktop is not linked to Arty Desktop",
                   "turn on the Arty Desktop link in starfire Desktop's settings and pair it"),
    "not_running": ("Arty Desktop is not running or is not answering", "start Arty Desktop on this PC"),
    "screen_control_off": ("screen control is turned off in Arty Desktop",
                           "turn on screen control and computer control in Arty Desktop's settings"),
    "desktop_outdated": ("this starfire Desktop is too old to control the PC", "update starfire Desktop"),
    REMOTE_MODEL: ("the chosen model runs in the cloud or on a remote connection",
                   "pick a model that runs on this home network"),
    MODEL_TOO_SMALL: ("the chosen model is too small to control the PC", "pick a larger local model"),
}
PAGE_UNOFFERED_REASONS = frozenset(UNOFFERED_REASONS) - {REMOTE_MODEL, MODEL_TOO_SMALL}
UNOFFERED_NOTE_START = "You cannot control the user's PC in this chat right now"


@dataclass(frozen=True)
class Field:
    kind: str
    limit: int = 0
    choices: tuple[str, ...] = ()
    rule: str = ""


@dataclass(frozen=True)
class Step:
    tool: str
    title: str
    fields: dict
    required: tuple[str, ...] = ()


STEPS = {
    "apps": Step("computer_apps", "list the open windows", {}),
    "open": Step("computer_open", "open an app", {"app": Field(TEXT, MAX_APP_CHARS, rule=APP_NAME)}, ("app",)),
    "click": Step("computer_click", "click", {
        "target": Field(TEXT, MAX_TARGET_CHARS), "x": Field(NUMBER, MAX_SCREEN_PIXEL),
        "y": Field(NUMBER, MAX_SCREEN_PIXEL), "button": Field(TEXT, choices=CLICK_BUTTONS),
        "double": Field(YES_OR_NO)}),
    "type": Step("computer_type", "type text", {"text": Field(TEXT, MAX_TYPED_CHARS, rule=TYPED_TEXT), "app": Field(TEXT, MAX_APP_CHARS, rule=APP_NAME)},
                 ("text",)),
    "key": Step("computer_key", "press keys", {"keys": Field(TEXT, MAX_KEYS_CHARS)}, ("keys",)),
    "scroll": Step("computer_scroll", "scroll", {
        "direction": Field(TEXT, choices=SCROLL_DIRECTIONS), "amount": Field(WHOLE_NUMBER, MAX_SCROLL_AMOUNT)}),
}
ACTIONS = tuple(STEPS) + (SCREENSHOT,)

_pending: dict[str, dict] = {}


class NotWaiting(LookupError):
    pass


class AlreadyClaimed(LookupError):
    pass


class BadAnswer(ValueError):
    pass


class BadStep(ValueError):
    pass


def _is_number(value) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _field_problem(name: str, field: Field, value, is_hidden) -> str | None:
    if field.kind == YES_OR_NO:
        return None if isinstance(value, bool) else f"{name} must be {YES_OR_NO}"
    if field.kind == NUMBER:
        return None if _is_number(value) and 0 <= value <= field.limit else f"{name} must be a number from 0 to {field.limit}"
    if field.kind == WHOLE_NUMBER:
        is_whole = isinstance(value, int) and not isinstance(value, bool)
        return None if is_whole and 1 <= value <= field.limit else f"{name} must be a whole number from 1 to {field.limit}"
    if field.choices:
        return None if value in field.choices else f"{name} must be one of " + ", ".join(field.choices)
    if not isinstance(value, str) or not value.strip():
        return f"{name} must be text that is not empty"
    if len(value) > field.limit:
        return f"{name} must be at most {field.limit} characters"
    if any(is_hidden(character) for character in value):
        return f"{name} must not contain line breaks, tabs or hidden characters"
    return _rule_problem(name, field.rule, value)


def _rule_problem(name: str, rule: str, value: str) -> str | None:
    if rule == TYPED_TEXT and as_arty_types_it(value) != value:
        return (f"{name} must be sent exactly as it should appear, with no HTML entities or escape codes: Arty Desktop "
                "would type it differently from what its card shows. Write the characters themselves, and press Enter "
                "or Tab with a key step")
    if rule == APP_NAME and (PATH_CHARACTERS & set(value) or value.lstrip().startswith(HIDDEN_FILE_START)):
        return f"{name} must be an app's name, not a path or an address: no / \\ ~ : and no leading ."
    return None


def as_arty_types_it(text: str) -> str:
    typed = text
    if not ARTY_CODE_OR_MARKUP.search(text):
        typed = html.unescape(text)
        if "\n" not in typed and "\\n" in typed and not ARTY_TYPED_LITERALLY.search(typed):
            typed = typed.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    return typed.replace("\r\n", "\n")


def _click_problem(arguments: dict) -> str | None:
    has_spot = "x" in arguments and "y" in arguments
    if "target" in arguments and ("x" in arguments or "y" in arguments):
        return "give either target or both x and y, not both"
    if "target" not in arguments and not has_spot:
        return "say what to click in target, or give both x and y"
    return None


def checked_step(action, arguments, is_hidden) -> dict:
    if action not in STEPS:
        raise BadStep("action must be one of " + ", ".join(ACTIONS))
    step = STEPS[action]
    unknown = sorted(set(arguments) - set(step.fields))
    if unknown:
        raise BadStep(f"{action} does not take {', '.join(unknown)}")
    missing = [name for name in step.required if name not in arguments]
    if missing:
        raise BadStep(f"{action} needs {', '.join(missing)}")
    for name, value in arguments.items():
        problem = _field_problem(name, step.fields[name], value, is_hidden)
        if problem:
            raise BadStep(problem)
    problem = _click_problem(arguments) if action == "click" else None
    if problem:
        raise BadStep(problem)
    return {"action": action, "arguments": dict(arguments)}


def title_of(action: str) -> str:
    step = STEPS.get(action)
    return f"{step.title} ({step.tool})" if step else action


def stage(step: dict, owner: str | None) -> str:
    request_id = uuid.uuid4().hex[:16]
    _pending[request_id] = {"step": step, "owner": owner, "claimed": False, "created": time.time(),
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
    return {"action": entry["step"]["action"], "arguments": dict(entry["step"]["arguments"])}


def _checked_result(outcome: dict) -> None:
    result = outcome.get("result")
    if not isinstance(result, str) or len(result) > MAX_RESULT_CHARS:
        raise BadAnswer(f"a finished step must say what happened in at most {MAX_RESULT_CHARS} characters")


def resolve(request_id: str, owner: str | None, outcome: dict) -> bool:
    try:
        entry = _own_entry(request_id, owner)
    except NotWaiting:
        return False
    if outcome.get("ok") is True:
        _checked_result(outcome)
    entry["future"].set_result(outcome)
    return True


async def wait_for_step(request_id: str) -> dict:
    entry = _pending.get(request_id)
    if entry is None:
        return {"ok": False, "reason": NO_ANSWER}
    try:
        return await asyncio.wait_for(asyncio.shield(entry["future"]), timeout=STEP_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return {"ok": False, "reason": NO_ANSWER}
    finally:
        _pending.pop(request_id, None)


def unoffered_note(reason: str) -> str:
    why, change = UNOFFERED_REASONS[reason]
    return (f"{UNOFFERED_NOTE_START}, because {why}. If they ask you to open, click, type or press anything on "
            f"their PC, say \"I can't control your PC right now because {why}\" and that they can {change}. "
            "Do not say you can only take screenshots.")


def runs_in_the_cloud(model: str) -> bool:
    name = (model or "").lower()
    return name.endswith(CLOUD_NAME_ENDING) or CLOUD_NAME_TAG in name
