from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass

from atomic_io import atomic_write_json

@dataclass
class EmailAccount:
    id: str
    label: str
    email_address: str
    imap_host: str
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 587
    username: str = ""

class EmailAccountStore:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "email_accounts.json")

    def _load(self) -> list[EmailAccount]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        accounts = []
        for item in raw:
            if not isinstance(item, dict) or "id" not in item or "email_address" not in item:
                continue
            accounts.append(EmailAccount(
                id=item["id"], label=item.get("label", item["email_address"]),
                email_address=item["email_address"],
                imap_host=item.get("imap_host", ""), imap_port=item.get("imap_port", 993),
                smtp_host=item.get("smtp_host", ""), smtp_port=item.get("smtp_port", 587),
                username=item.get("username", ""),
            ))
        return accounts

    def _save(self, accounts: list[EmailAccount]) -> None:
        atomic_write_json(self.path, [asdict(a) for a in accounts])

    def list(self) -> list[EmailAccount]:
        return self._load()

    def get(self, account_id: str) -> EmailAccount | None:
        for a in self._load():
            if a.id == account_id:
                return a
        return None

    def add(self, label: str, email_address: str, imap_host: str, imap_port: int,
            smtp_host: str, smtp_port: int, username: str = "") -> EmailAccount:
        accounts = self._load()
        account = EmailAccount(
            id=uuid.uuid4().hex[:12], label=label or email_address, email_address=email_address,
            imap_host=imap_host, imap_port=imap_port, smtp_host=smtp_host, smtp_port=smtp_port,
            username=username or email_address,
        )
        accounts.append(account)
        self._save(accounts)
        return account

    def delete(self, account_id: str) -> bool:
        accounts = self._load()
        remaining = [a for a in accounts if a.id != account_id]
        if len(remaining) == len(accounts):
            return False
        self._save(remaining)
        return True

VALID_MATCH_FIELDS = {"from", "subject"}
VALID_ACTIONS = {"mark_read", "archive", "delete", "add_note", "ai_summarize_note"}

@dataclass
class EmailRule:
    id: str
    account_id: str
    folder: str = "INBOX"
    match_field: str = "from"
    match_value: str = ""
    action: str = "add_note"
    endpoint_id: str = ""
    model: str = ""
    enabled: bool = True
    last_seen_uid: int = 0

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
