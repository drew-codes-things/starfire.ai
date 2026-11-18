from __future__ import annotations

import fnmatch
import json
import os

from atomic_io import atomic_write_json, holds_file_lock, report_unreadable_store

ACCESS_FILE = "model_access.json"
MAX_PATTERNS = 100
MAX_PATTERN_CHARS = 200
LATEST_TAG = ":latest"
WILDCARD_CHARACTERS = frozenset("*?[")


class ModelAccessError(ValueError):
    pass


def _without_tag(name: str) -> str:
    name = name.split("@", 1)[0]
    head, colon, tag = name.rpartition(":")
    return head if colon and head and "/" not in tag else name


def exact_names(model: str) -> set[str]:
    spelled = model.strip().lower()
    return {spelled, _without_tag(spelled)}


def name_forms(model: str) -> set[str]:
    forms = set()
    for name in exact_names(model):
        parts = name.split("/")
        forms.update("/".join(parts[start:]) for start in range(len(parts)))
    return forms


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
    entries = [entry.strip().lower() for entry in allowed
               if entry.strip() and not WILDCARD_CHARACTERS.intersection(entry)]
    names = _same_model_names(model.strip().lower())
    return any(names & _same_model_names(entry) for entry in entries)


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
