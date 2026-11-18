from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from atomic_io import atomic_write_json, holds_file_lock, report_unreadable_store
from model_access import matches_any

AUTO = "auto"
VISION, CODE, AGENT, CHAT = "vision", "code", "agent", "chat"
ROLES = (VISION, CODE, AGENT, CHAT)
SETTINGS_FILE = "auto_model.json"
MAX_MODEL_NAME_CHARS = 200

VISION_NAME_PATTERNS = ("*vl*", "*vision*", "*llava*")
CODE_NAME_PATTERNS = ("*code*",)
SEEN_KINDS = frozenset({"image", "video"})
HEARD_KINDS = frozenset({"audio"})
CLASSIFIED_CHARS_AT_EACH_END = 4_000

ADDRESSED_TO_ARTY = re.compile(r"^\W*(hey\s+|ok\s+|okay\s+)?arty\b", re.IGNORECASE)
CODE_SIGNS = (
    re.compile(r"```"),
    re.compile(r"Traceback \(most recent call last\)"),
    re.compile(r"^[^\S\n]+at [\w.$<>]+\(.*:\d+(:\d+)?\)", re.MULTILINE),
    re.compile(r"\b[A-Z]\w*(Error|Exception)\b"),
    re.compile(r"(?<=[\w/-])\.(py|js|mjs|ts|tsx|jsx|go|rs|java|kt|cpp|hpp|cs|rb|php|sh|sql|html|css|ya?ml|toml)\b",
               re.IGNORECASE),
    re.compile(r"\b(code|coding|refactor|debug|debugging|compile|compiler|regex|stack trace|unit tests?|"
               r"syntax error|python|javascript|typescript|golang|kotlin|bash script|shell script|sql query|"
               r"write a function|this function|git (commit|rebase|merge|diff))\b", re.IGNORECASE),
    re.compile(r"\bc\+\+", re.IGNORECASE),
)
AGENT_SIGNS = re.compile(
    r"\b(remind me|set (a|an) (timer|alarm|reminder)|my (phone|calendar|inbox|emails?|schedule|lights?)|"
    r"turn (on|off) the|switch (on|off) the|play (some|me|a song)|search the web|search online|"
    r"look (it |this |that )?up online|on the web|send (a |an )?(text|message|email)|weather|"
    r"latest news|news today)\b", re.IGNORECASE)


class AutoModelError(ValueError):
    pass


def _classified_part(text: str) -> str:
    if len(text) <= 2 * CLASSIFIED_CHARS_AT_EACH_END:
        return text
    return text[:CLASSIFIED_CHARS_AT_EACH_END] + "\n" + text[-CLASSIFIED_CHARS_AT_EACH_END:]


def prompt_role(text: str, attachment_kinds: set[str]) -> str:
    text = _classified_part(text)
    if attachment_kinds & SEEN_KINDS:
        return VISION
    if attachment_kinds & HEARD_KINDS or ADDRESSED_TO_ARTY.search(text):
        return AGENT
    if any(sign.search(text) for sign in CODE_SIGNS):
        return CODE
    if AGENT_SIGNS.search(text):
        return AGENT
    return CHAT


@dataclass(frozen=True)
class RoleSources:
    configured: dict[str, str] = field(default_factory=dict)
    task_model: str = ""
    own_tools_patterns: list[str] = field(default_factory=list)
    restricted_patterns: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AutoPick:
    endpoint_id: str
    model: str
    role: str
    filled_by: str

    def event(self) -> dict:
        return {"auto": True, "endpoint_id": self.endpoint_id, "model": self.model,
                "role": self.role, "filled_by": self.filled_by}


def _named(models: list[dict], name: str) -> dict | None:
    return next((m for m in models if m["id"] == name), None)


def _first_matching(models: list[dict], patterns) -> dict | None:
    for pattern in patterns:
        found = next((m for m in models if matches_any(m["id"], [pattern])), None)
        if found:
            return found
    return None


def _unrestricted(models: list[dict], sources: RoleSources) -> list[dict]:
    return [m for m in models if not matches_any(m["id"], sources.restricted_patterns)]


def _general(models: list[dict], sources: RoleSources) -> list[dict]:
    return [m for m in _unrestricted(models, sources)
            if not matches_any(m["id"], sources.own_tools_patterns) and not matches_any(m["id"], VISION_NAME_PATTERNS)]


def _guessed_for(role: str, usable: list[dict], sources: RoleSources) -> dict | None:
    general = _general(usable, sources)
    if role == VISION:
        seeing = [m for m in _unrestricted(usable, sources) if not matches_any(m["id"], sources.own_tools_patterns)]
        return _first_matching(seeing, VISION_NAME_PATTERNS)
    if role == CODE:
        return _first_matching(general, CODE_NAME_PATTERNS)
    if role == AGENT:
        return _first_matching(_unrestricted(usable, sources), sources.own_tools_patterns)
    return (_named(general, sources.task_model) if sources.task_model else None) or (general[0] if general else None)


def model_for_role(role: str, usable: list[dict], sources: RoleSources) -> dict | None:
    configured = sources.configured.get(role)
    chosen = _named(usable, configured) if configured else None
    return chosen or _guessed_for(role, usable, sources)


def choose(role: str, usable: list[dict], sources: RoleSources) -> AutoPick | None:
    for filling_role in dict.fromkeys((role, CHAT)):
        model = model_for_role(filling_role, usable, sources)
        if model:
            return AutoPick(model["endpoint_id"], model["id"], role, filling_role)
    leftover = _unrestricted(usable, sources)
    if leftover:
        return AutoPick(leftover[0]["endpoint_id"], leftover[0]["id"], role, CHAT)
    return None


def cleaned_roles(roles) -> dict[str, str]:
    if not isinstance(roles, dict) or any(role not in ROLES for role in roles):
        raise AutoModelError(f"roles must be an object with only these keys: {', '.join(ROLES)}")
    cleaned = {}
    for role, name in roles.items():
        if name is None:
            name = ""
        if not isinstance(name, str) or len(name.strip()) > MAX_MODEL_NAME_CHARS:
            raise AutoModelError(f"{role} must be a model name of up to {MAX_MODEL_NAME_CHARS} characters, "
                                 "or empty to let Auto choose")
        cleaned[role] = name.strip()
    return cleaned


class AutoModelSettings:

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, SETTINGS_FILE)

    def roles(self) -> dict[str, str]:
        if not os.path.exists(self.path):
            return {role: "" for role in ROLES}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as error:
            report_unreadable_store(self.path, error)
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        return {role: raw[role] if isinstance(raw.get(role), str) else "" for role in ROLES}

    @holds_file_lock()
    def set_roles(self, roles: dict[str, str]) -> dict[str, str]:
        updated = {**self.roles(), **roles}
        atomic_write_json(self.path, updated)
        return updated
