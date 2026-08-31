"""Email rules: "when a message matching X arrives in this account/folder,
do Y" — checked automatically by a background poller (email_rule_checker.py)
rather than only when you happen to open the folder yourself.

This is a genuine change from this app's previously-documented "no
background inbox sync" design. That claim stays true for ordinary browsing
(every read/list action still opens a live connection on demand, nothing
cached) — but rule matching specifically needs to notice new mail without
you looking, so accounts with at least one enabled rule now also get a
periodic IMAP check. See email_rule_checker.py for the poll loop.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass

from atomic_io import atomic_write_json

VALID_MATCH_FIELDS = {"from", "subject"}
VALID_ACTIONS = {"mark_read", "archive", "delete", "add_note", "ai_summarize_note"}


@dataclass
class EmailRule:
    id: str
    account_id: str
    folder: str = "INBOX"
    match_field: str = "from"  # from | subject
    match_value: str = ""      # case-insensitive substring match
    action: str = "add_note"
    endpoint_id: str = ""      # required for action=ai_summarize_note
    model: str = ""            # required for action=ai_summarize_note
    enabled: bool = True
    last_seen_uid: int = 0     # highest message UID already processed for this rule


class EmailRuleStore:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "email_rules.json")

    def _load(self) -> list[EmailRule]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        rules = []
        for item in raw:
            if not isinstance(item, dict) or "id" not in item or "account_id" not in item:
                continue
            rules.append(EmailRule(
                id=item["id"], account_id=item["account_id"], folder=item.get("folder", "INBOX"),
                match_field=item.get("match_field", "from"), match_value=item.get("match_value", ""),
                action=item.get("action", "add_note"), endpoint_id=item.get("endpoint_id", ""),
                model=item.get("model", ""), enabled=item.get("enabled", True),
                last_seen_uid=item.get("last_seen_uid", 0),
            ))
        return rules

    def _save(self, rules: list[EmailRule]) -> None:
        atomic_write_json(self.path, [asdict(r) for r in rules])

    def list(self) -> list[EmailRule]:
        return self._load()

    def get(self, rule_id: str) -> EmailRule | None:
        for r in self._load():
            if r.id == rule_id:
                return r
        return None

    def add(self, **fields) -> EmailRule:
        rules = self._load()
        if fields.get("match_field") not in VALID_MATCH_FIELDS:
            fields["match_field"] = "from"
        if fields.get("action") not in VALID_ACTIONS:
            fields["action"] = "add_note"
        rule = EmailRule(id=uuid.uuid4().hex[:12], **fields)
        rules.append(rule)
        self._save(rules)
        return rule

    def update(self, rule_id: str, **fields) -> bool:
        rules = self._load()
        found = False
        for r in rules:
            if r.id == rule_id:
                for k, v in fields.items():
                    if v is not None and hasattr(r, k):
                        setattr(r, k, v)
                found = True
        if found:
            self._save(rules)
        return found

    def delete(self, rule_id: str) -> bool:
        rules = self._load()
        remaining = [r for r in rules if r.id != rule_id]
        if len(remaining) == len(rules):
            return False
        self._save(remaining)
        return True
