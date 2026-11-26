from __future__ import annotations

import fnmatch
import json
import os
import re

from atomic_io import atomic_write_json, holds_file_lock, report_unreadable_store

ACCESS_FILE = "model_access.json"
MAX_PATTERNS = 1000
MAX_PATTERN_CHARS = 200
LATEST_TAG = ":latest"
WILDCARD_CHARACTERS = frozenset("*?[")
ESCAPED_WILDCARD = re.compile(r"\[([*?[])\]")
PATH_SEPARATORS = re.compile(r"[\\/]")
TRAILING_SEPARATORS = re.compile(r"[\\/\s]+$")
COPY_NUMBER = re.compile(r"\s*\(\d+\)$")
DOT = "."
TAG_BREAKERS = frozenset("/\\")
NEW_ACCOUNTS_KEY = "new-accounts"


class ModelAccessError(ValueError):
    pass


def _without_tag(name: str) -> str:
    name = name.split("@", 1)[0]
    head, colon, tag = name.rpartition(":")
    return head if colon and head and not TAG_BREAKERS.intersection(tag) else name


def exact_names(model: str) -> set[str]:
    spelled = model.strip().lower()
    return {spelled, _without_tag(spelled)}


def _between_digits(name: str, at: int) -> bool:
    return 0 < at < len(name) - 1 and name[at - 1].isdigit() and name[at + 1].isdigit()


def _dot_prefixes(name: str) -> set[str]:
    return {name[:at] for at, character in enumerate(name) if character == DOT and not _between_digits(name, at)}


def _without_copy_number(name: str) -> str:
    return COPY_NUMBER.sub("", name)


def name_forms(model: str) -> set[str]:
    forms = set()
    for name in exact_names(model) | exact_names(TRAILING_SEPARATORS.sub("", model)):
        parts = PATH_SEPARATORS.split(name)
        tails = {"/".join(parts[start:]) for start in range(len(parts))}
        forms.update(tails)
        forms.update(prefix for tail in tails for prefix in _dot_prefixes(tail))
    forms |= {_without_copy_number(form) for form in forms}
    return forms | {form.replace("_", "-") for form in forms}


def literal_name(entry: str) -> str | None:
    unescaped = ESCAPED_WILDCARD.sub("", entry)
    if WILDCARD_CHARACTERS.intersection(unescaped):
        return None
    return ESCAPED_WILDCARD.sub(r"\1", entry)


def escaped_name(name: str) -> str:
    return "".join(f"[{character}]" if character in WILDCARD_CHARACTERS else character for character in name)


def _any_form_matches(forms: set[str], patterns: list[str]) -> bool:
    lowered = [pattern.strip().lower() for pattern in patterns if pattern.strip()]
    return any(fnmatch.fnmatchcase(form, pattern) for form in forms for pattern in lowered)


def matches_any(model: str, patterns: list[str]) -> bool:
    return _any_form_matches(name_forms(model), patterns)


def _same_model_names(name: str) -> set[str]:
    if name.endswith(LATEST_TAG):
        return {name, name[:-len(LATEST_TAG)]}
    return {name, name + LATEST_TAG} if _without_tag(name) == name else {name}


def _named_exactly(model: str, allowed: list[str]) -> bool:
    literals = (literal_name(entry.strip()) for entry in allowed if entry.strip())
    names = _same_model_names(model.strip().lower())
    return any(names & _same_model_names(literal.lower()) for literal in literals if literal)


def may_use(model: str, is_admin: bool, allowed: list[str] | None, restricted: list[str]) -> bool:
    if is_admin:
        return True
    if allowed is not None and _named_exactly(model, allowed):
        return True
    if matches_any(model, restricted):
        return False
    return allowed is None or _any_form_matches(exact_names(model), allowed)


def cleaned_patterns(patterns) -> list[str] | None:
    if patterns is None:
        return None
    if not isinstance(patterns, list) or not all(isinstance(pattern, str) for pattern in patterns):
        raise ModelAccessError("models must be a list of model names or patterns, or null for the default")
    kept = list(dict.fromkeys(pattern.strip() for pattern in patterns if pattern.strip()))
    if len(kept) > MAX_PATTERNS or any(len(pattern) > MAX_PATTERN_CHARS for pattern in kept):
        raise ModelAccessError(f"at most {MAX_PATTERNS} models of up to {MAX_PATTERN_CHARS} characters each")
    return kept


def is_pattern(entry: str) -> bool:
    return literal_name(entry) is None


def access_view(models: list[dict], allowed: list[str] | None, restricted: list[str]) -> dict:
    entries = allowed or []
    listed = []
    for model in models:
        name = model["id"]
        usable = may_use(name, False, allowed, restricted)
        named = allowed is not None and _named_exactly(name, entries)
        listed.append({**model, "restricted": matches_any(name, restricted), "allowed": usable,
                       "by_pattern": usable and allowed is not None and not named})
    unmatched = [entry for entry in entries
                 if is_pattern(entry) or not any(_named_exactly(model["id"], [entry]) for model in models)]
    return {"models": allowed, "restricted": restricted, "available": listed, "kept": unmatched,
            "max_models": MAX_PATTERNS}


class ModelAccessStore:

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, ACCESS_FILE)

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as error:
            report_unreadable_store(self.path, error)
            return {}
        return raw if isinstance(raw, dict) else {}

    def allowed_for(self, user_id: str | None) -> list[str] | None:
        allowed = self._load().get(user_id) if user_id else None
        return [p for p in allowed if isinstance(p, str)] if isinstance(allowed, list) else None

    @holds_file_lock()
    def set_allowed(self, user_id: str, patterns: list[str] | None) -> list[str] | None:
        everyone = self._load()
        if patterns is None:
            everyone.pop(user_id, None)
        else:
            everyone[user_id] = patterns
        atomic_write_json(self.path, everyone)
        return patterns

    def default_for_new_accounts(self) -> list[str] | None:
        return self.allowed_for(NEW_ACCOUNTS_KEY)

    def set_default_for_new_accounts(self, patterns: list[str] | None) -> list[str] | None:
        return self.set_allowed(NEW_ACCOUNTS_KEY, patterns)

    def apply_default_to(self, user_id: str) -> None:
        patterns = self.default_for_new_accounts()
        if patterns is not None:
            self.set_allowed(user_id, patterns)
