from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass

from atomic_io import atomic_write_json

COMMAND_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

@dataclass
class Prompt:
    id: str
    command: str
    title: str
    content: str

class PromptStore:

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "prompts.json")

    def _load(self) -> list[Prompt]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        return [Prompt(id=item["id"], command=item["command"], title=item.get("title", ""),
                       content=item.get("content", ""))
                for item in raw if isinstance(item, dict) and "id" in item and "command" in item]

    def _save(self, prompts: list[Prompt]) -> None:
        atomic_write_json(self.path, [asdict(p) for p in prompts])

    def _validate(self, prompts: list[Prompt], command: str, content: str, own_id: str = "") -> str:
        command = command.strip().lstrip("/").lower()
        if not COMMAND_RE.match(command):
            raise ValueError("command must be 1-32 characters: lowercase letters, digits, - or _")
        if not content.strip():
            raise ValueError("prompt content is empty")
        if any(p.command == command and p.id != own_id for p in prompts):
            raise ValueError(f"/{command} is already used by another prompt")
        return command

    def list(self) -> list[Prompt]:
        return sorted(self._load(), key=lambda p: p.command)

    def add(self, command: str, title: str, content: str) -> Prompt:
        prompts = self._load()
        command = self._validate(prompts, command, content)
        prompt = Prompt(id=uuid.uuid4().hex[:12], command=command, title=title.strip() or command, content=content)
        prompts.append(prompt)
        self._save(prompts)
        return prompt

    def update(self, prompt_id: str, command: str, title: str, content: str) -> bool:
        prompts = self._load()
        prompt = next((p for p in prompts if p.id == prompt_id), None)
        if not prompt:
            return False
        prompt.command = self._validate(prompts, command, content, own_id=prompt_id)
        prompt.title = title.strip() or prompt.command
        prompt.content = content
        self._save(prompts)
        return True

    def delete(self, prompt_id: str) -> bool:
        prompts = self._load()
        remaining = [p for p in prompts if p.id != prompt_id]
        if len(remaining) == len(prompts):
            return False
        self._save(remaining)
        return True
