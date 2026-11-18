from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass

from atomic_io import atomic_write_json, holds_file_lock, report_unreadable_store

POLICY_FILE = "approval_policy.json"


@dataclass(frozen=True)
class ApprovalPolicy:
    require_shell_approval: bool = True
    require_edit_approval: bool = True

    def tightened_by(self, require_shell_approval: bool | None, require_edit_approval: bool | None) -> ApprovalPolicy:
        return ApprovalPolicy(
            require_shell_approval=self.require_shell_approval or require_shell_approval is True,
            require_edit_approval=self.require_edit_approval or require_edit_approval is True,
        )


class ApprovalPolicyStore:

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, POLICY_FILE)

    def get(self) -> ApprovalPolicy:
        if not os.path.exists(self.path):
            return ApprovalPolicy()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as error:
            report_unreadable_store(self.path, error)
            return ApprovalPolicy()
        if not isinstance(raw, dict):
            return ApprovalPolicy()
        return ApprovalPolicy(
            require_shell_approval=raw.get("require_shell_approval") is not False,
            require_edit_approval=raw.get("require_edit_approval") is not False,
        )

    @holds_file_lock()
    def update(self, require_shell_approval: bool | None = None,
               require_edit_approval: bool | None = None) -> ApprovalPolicy:
        current = self.get()
        updated = ApprovalPolicy(
            require_shell_approval=current.require_shell_approval if require_shell_approval is None
            else require_shell_approval,
            require_edit_approval=current.require_edit_approval if require_edit_approval is None
            else require_edit_approval,
        )
        atomic_write_json(self.path, asdict(updated))
        return updated
