"""Email account registry: metadata only (host/port/username), same JSON
CRUD shape as model_endpoints.py/mcp_servers_store.py. The app password
itself is NOT stored here — it goes through the existing api_key_manager.py
(the same encrypted store already protecting provider API keys), keyed by
account id, so this pass doesn't need any new secret-storage code.
"""

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
    username: str = ""  # defaults to email_address if blank


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
