from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field

from cryptography.fernet import InvalidToken

from api_key_manager import APIKeyManager
from atomic_io import atomic_write_json, holds_file_lock, report_unreadable_store

logger = logging.getLogger(__name__)

TRANSPORTS = ("stdio", "http", "sse")
SECRET_FIELDS = ("env", "headers")
SECRETS_ENCRYPTED = "secrets_encrypted"

@dataclass
class McpServerConfig:
    id: str
    name: str
    command: str
    args: list[str]
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    transport: str = "stdio"
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)

REFERENCE_SERVERS = {
    "filesystem": {
        "name": "Filesystem",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        "needs_path": True,
    },
    "fetch": {
        "name": "Fetch",
        "command": "uvx",
        "args": ["mcp-server-fetch"],
        "needs_path": False,
    },
    "memory": {
        "name": "Memory",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
        "needs_path": False,
    },
}

MCP_SERVER_REPOSITORY = {
    "git": {
        "name": "Git",
        "command": "uvx",
        "args": ["mcp-server-git", "--repository"],
        "needs_path": True,
        "env_fields": [],
    },
    "github": {
        "name": "GitHub",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "needs_path": False,
        "env_fields": [{"key": "GITHUB_PERSONAL_ACCESS_TOKEN", "label": "GitHub personal access token"}],
    },
    "gitlab": {
        "name": "GitLab",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-gitlab"],
        "needs_path": False,
        "env_fields": [{"key": "GITLAB_PERSONAL_ACCESS_TOKEN", "label": "GitLab personal access token"}],
    },
    "sqlite": {
        "name": "SQLite",
        "command": "uvx",
        "args": ["--with", "mcp==1.0.0", "mcp-server-sqlite", "--db-path"],
        "needs_path": True,
        "env_fields": [],
    },
    "brave_search": {
        "name": "Brave Search",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-brave-search"],
        "needs_path": False,
        "env_fields": [{"key": "BRAVE_API_KEY", "label": "Brave Search API key"}],
    },
    "slack": {
        "name": "Slack",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-slack"],
        "needs_path": False,
        "env_fields": [
            {"key": "SLACK_BOT_TOKEN", "label": "Slack bot token"},
            {"key": "SLACK_TEAM_ID", "label": "Slack team ID"},
        ],
    },
    "puppeteer": {
        "name": "Puppeteer (browser automation)",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-puppeteer"],
        "needs_path": False,
        "env_fields": [],
    },
}

class McpServerStore:
    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "mcp_servers.json")
        self.cipher = APIKeyManager(data_dir)

    @holds_file_lock()
    def _read_items(self) -> list:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as error:
            report_unreadable_store(self.path, error)
            return []
        if not isinstance(raw, list):
            return []
        if any(_holds_plain_secrets(item) for item in raw):
            raw = self._encrypt_plain_secrets(raw)
        return raw

    def _encrypt_plain_secrets(self, items: list) -> list:
        migrated = [self._with_encrypted_secrets(item) if _holds_plain_secrets(item) else item for item in items]
        try:
            self._write_items(migrated)
        except OSError as error:
            logger.error("could not rewrite %s with its MCP secrets encrypted, they stay in plain text on disk "
                         "until the next save: %s", self.path, error)
        else:
            logger.info("encrypted the MCP server env and header values stored in %s", self.path)
        return migrated

    def _with_encrypted_secrets(self, item: dict) -> dict:
        encrypted = dict(item, **{SECRETS_ENCRYPTED: True})
        for secret_field in SECRET_FIELDS:
            values = item.get(secret_field)
            if isinstance(values, dict):
                encrypted[secret_field] = {key: self.cipher.encrypt_api_key(value) if isinstance(value, str) else value
                                           for key, value in values.items()}
        return encrypted

    def _decrypted_or_none(self, value) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            return self.cipher.decrypt_api_key(value)
        except (InvalidToken, ValueError):
            return None

    def _decrypted(self, server_name: str, secret_field: str, values) -> dict[str, str]:
        if not isinstance(values, dict):
            return {}
        plain = {}
        for key, value in values.items():
            decrypted = self._decrypted_or_none(value)
            if decrypted is None:
                logger.warning("could not decrypt %s %s of MCP server %s (saved by another install?), "
                               "starting it without that value", secret_field, key, server_name)
                continue
            plain[key] = decrypted
        return plain

    def _to_config(self, item: dict) -> McpServerConfig:
        name = item.get("name") or item.get("command") or item.get("url", "")
        return McpServerConfig(
            id=item["id"],
            name=name,
            command=item.get("command", ""),
            args=item.get("args", []),
            env=self._decrypted(name, "env", item.get("env", {})),
            enabled=item.get("enabled", True),
            transport=item.get("transport", "stdio") if item.get("transport") in TRANSPORTS else "stdio",
            url=item.get("url", ""),
            headers=self._decrypted(name, "header", item.get("headers", {})),
        )

    def _load(self) -> list[McpServerConfig]:
        return [self._to_config(item) for item in self._read_items() if _is_usable(item)]

    def _write_items(self, items: list) -> None:
        atomic_write_json(self.path, items)

    def list(self) -> list[McpServerConfig]:
        return self._load()

    def get(self, server_id: str) -> McpServerConfig | None:
        for s in self._load():
            if s.id == server_id:
                return s
        return None

    @holds_file_lock()
    def add(self, name: str, command: str, args: list[str], env: dict[str, str] | None = None,
            transport: str = "stdio", url: str = "", headers: dict[str, str] | None = None) -> McpServerConfig:
        items = self._read_items()
        server = McpServerConfig(id=uuid.uuid4().hex[:12], name=name, command=command,
                                  args=args, env=env or {}, transport=transport, url=url, headers=headers or {})
        items.append(self._with_encrypted_secrets(asdict(server)))
        self._write_items(items)
        return server

    @holds_file_lock()
    def set_enabled(self, server_id: str, enabled: bool) -> bool:
        items = self._read_items()
        found = False
        for item in items:
            if _is_usable(item) and item["id"] == server_id:
                item["enabled"] = enabled
                found = True
        if found:
            self._write_items(items)
        return found

    @holds_file_lock()
    def delete(self, server_id: str) -> bool:
        items = self._read_items()
        remaining = [item for item in items if not (_is_usable(item) and item["id"] == server_id)]
        if len(remaining) == len(items):
            return False
        self._write_items(remaining)
        return True


def _is_usable(item) -> bool:
    return isinstance(item, dict) and "id" in item and bool(item.get("command") or item.get("url"))


def _holds_plain_secrets(item) -> bool:
    return isinstance(item, dict) and not item.get(SECRETS_ENCRYPTED)


def without_secret_values(items: list) -> list:
    blanked = []
    for item in items:
        if isinstance(item, dict):
            item = dict(item, **{SECRETS_ENCRYPTED: True})
            for secret_field in SECRET_FIELDS:
                if isinstance(item.get(secret_field), dict):
                    item[secret_field] = {key: "" for key in item[secret_field]}
        blanked.append(item)
    return blanked


def has_missing_secrets(server: McpServerConfig) -> bool:
    return any(value == "" for value in (*server.env.values(), *server.headers.values()))
