from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from cryptography.fernet import InvalidToken

from api_key_manager import APIKeyManager
from atomic_io import atomic_write_json, holds_file_lock, report_unreadable_store

logger = logging.getLogger(__name__)

TRANSPORTS = ("stdio", "http", "sse")
SECRET_FIELDS = ("env", "headers")
SECRETS_ENCRYPTED = "secrets_encrypted"
FERNET_VERSION_BYTE = 0x80
FERNET_FIXED_BYTES = 57
FERNET_BLOCK_BYTES = 16
FERNET_TOKEN_TEXT = re.compile(r"[A-Za-z0-9_-]+={0,2}")
SECRET_REMOVED = "__secret_removed__"
SECRET_NAME_WORDS = frozenset({"key", "auth", "authorization", "pwd", "pass", "passphrase", "sig", "signature",
                               "credential", "credentials", "bearer"})
SECRET_NAME_ENDINGS = ("token", "secret", "password", "passwd", "apikey")
NAME_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
URL_START = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
NAMED_VALUE = re.compile(r"(-{0,2}[A-Za-z_][\w.-]*)(=|:\s*)(.*)", re.S)

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
            logger.info("encrypted the MCP server env, header, argument and URL values stored in %s", self.path)
        return migrated

    def _encrypted_if_plain(self, value):
        return self.cipher.encrypt_api_key(value) if _is_plain_text(value) else value

    def _with_encrypted_secrets(self, item: dict) -> dict:
        encrypted = dict(item, **{SECRETS_ENCRYPTED: True})
        for secret_field in SECRET_FIELDS:
            values = item.get(secret_field)
            if isinstance(values, dict):
                encrypted[secret_field] = {key: self._encrypted_if_plain(value) for key, value in values.items()}
        if isinstance(item.get("args"), list):
            encrypted["args"] = [self._encrypted_if_plain(arg) for arg in item["args"]]
        encrypted["url"] = self._encrypted_if_plain(item.get("url", ""))
        if isinstance(item.get("name"), str):
            encrypted["name"] = without_text_credentials(item["name"])
        return encrypted

    def _decrypted_or_none(self, value) -> str | None:
        if not isinstance(value, str):
            return None
        if not _looks_like_token(value):
            return value
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
                               "it needs entering again", secret_field, key, server_name)
                decrypted = ""
            plain[key] = decrypted
        return plain

    def _plain_or_removed(self, server_name: str, what: str, value) -> str:
        decrypted = self._decrypted_or_none(value)
        if decrypted is None:
            logger.warning("could not decrypt the %s of MCP server %s (saved by another install?), "
                           "it needs entering again", what, server_name)
            return SECRET_REMOVED
        return decrypted

    def _plain_args(self, server_name: str, args) -> list[str]:
        if not isinstance(args, list):
            return []
        return [self._plain_or_removed(server_name, f"argument {position}", arg)
                for position, arg in enumerate(args, start=1)]

    def _to_config(self, item: dict) -> McpServerConfig:
        label = item.get("name") or item.get("command") or item["id"]
        url = self._plain_or_removed(label, "URL", item.get("url") or "")
        name = item.get("name") or item.get("command") or without_url_credentials(url)
        return McpServerConfig(
            id=item["id"],
            name=name,
            command=item.get("command", ""),
            args=self._plain_args(name, item.get("args")),
            env=self._decrypted(name, "env", item.get("env", {})),
            enabled=item.get("enabled", True),
            transport=item.get("transport", "stdio") if item.get("transport") in TRANSPORTS else "stdio",
            url=url,
            headers=self._decrypted(name, "header", item.get("headers", {})),
        )

    def _backup_copy(self, item: dict) -> dict:
        label = item.get("name") or item.get("command") or item.get("id", "")
        copy = dict(item, **{SECRETS_ENCRYPTED: True})
        for secret_field in SECRET_FIELDS:
            if isinstance(copy.get(secret_field), dict):
                copy[secret_field] = {key: "" for key in copy[secret_field]}
        if isinstance(item.get("args"), list):
            copy["args"] = without_arg_credentials(self._plain_args(label, item["args"]))
        copy["url"] = without_url_credentials(self._plain_or_removed(label, "URL", item.get("url") or ""))
        if isinstance(item.get("name"), str):
            copy["name"] = without_text_credentials(item["name"])
        return copy

    def without_secret_values(self, items: list) -> list:
        return [self._backup_copy(item) if isinstance(item, dict) else item for item in items]

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
    def restore(self, restored_items: list) -> None:
        live_by_id = {item["id"]: item for item in self._read_items() if _is_usable(item)}
        merged = [_with_live_secrets(item, live_by_id[item["id"]]) if _is_usable(item) and item["id"] in live_by_id
                  else item for item in restored_items]
        self._write_items([self._with_encrypted_secrets(item) if isinstance(item, dict) else item for item in merged])

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


def _looks_like_token(value: str) -> bool:
    if not FERNET_TOKEN_TEXT.fullmatch(value):
        return False
    try:
        raw = base64.urlsafe_b64decode(value)
    except (binascii.Error, ValueError):
        return False
    is_whole_blocks = (len(raw) - FERNET_FIXED_BYTES) % FERNET_BLOCK_BYTES == 0
    return len(raw) > FERNET_FIXED_BYTES and raw[0] == FERNET_VERSION_BYTE and is_whole_blocks


def _is_plain_text(value) -> bool:
    return isinstance(value, str) and value != "" and not _looks_like_token(value)


def _secret_values(item: dict) -> list:
    values = [item.get("url")]
    for secret_field in SECRET_FIELDS:
        if isinstance(item.get(secret_field), dict):
            values.extend(item[secret_field].values())
    if isinstance(item.get("args"), list):
        values.extend(item["args"])
    return values


def _holds_plain_secrets(item) -> bool:
    return isinstance(item, dict) and any(_is_plain_text(value) for value in _secret_values(item))


def _holds_removed_secret(value) -> bool:
    if isinstance(value, list):
        return any(_holds_removed_secret(part) for part in value)
    return isinstance(value, str) and SECRET_REMOVED in value


def _with_live_secrets(restored: dict, live: dict) -> dict:
    merged = dict(restored)
    for secret_field in SECRET_FIELDS:
        restored_values, live_values = restored.get(secret_field), live.get(secret_field)
        if isinstance(restored_values, dict) and isinstance(live_values, dict):
            merged[secret_field] = {key: live_values[key] if value == "" and key in live_values else value
                                    for key, value in restored_values.items()}
    for removable_field in ("args", "url"):
        if _holds_removed_secret(restored.get(removable_field)) and removable_field in live:
            merged[removable_field] = live[removable_field]
    return merged


def _names_a_secret(name: str) -> bool:
    words = [word.lower() for word in NAME_WORD.findall(name)]
    return any(word in SECRET_NAME_WORDS or word.endswith(SECRET_NAME_ENDINGS) for word in words)


def _params_without_secrets(params: str) -> str:
    if not params:
        return params
    kept = []
    for pair in params.split("&"):
        name, equals, value = pair.partition("=")
        if equals and value and _names_a_secret(unquote_plus(name)):
            pair = f"{name}={SECRET_REMOVED}"
        kept.append(pair)
    return "&".join(kept)


def _netloc_without_password(netloc: str) -> str:
    userinfo, at, host = netloc.rpartition("@")
    user, _colon, password = userinfo.partition(":")
    if not at or not password:
        return netloc
    return f"{user}:{SECRET_REMOVED}@{host}"


def without_url_credentials(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return SECRET_REMOVED
    cleaned = parts._replace(netloc=_netloc_without_password(parts.netloc),
                             query=_params_without_secrets(parts.query),
                             fragment=_params_without_secrets(parts.fragment))
    return url if cleaned == parts else urlunsplit(cleaned)


def without_text_credentials(text: str) -> str:
    if URL_START.match(text):
        return without_url_credentials(text)
    named = NAMED_VALUE.fullmatch(text)
    if not named:
        return text
    name, separator, value = named.groups()
    if value and _names_a_secret(name):
        return f"{name}{separator}{SECRET_REMOVED}"
    return f"{name}{separator}{without_text_credentials(value)}"


def _is_secret_flag(arg) -> bool:
    return isinstance(arg, str) and arg.startswith("-") and "=" not in arg and _names_a_secret(arg)


def without_arg_credentials(args: list) -> list:
    cleaned = []
    for position, arg in enumerate(args):
        follows_secret_flag = position > 0 and _is_secret_flag(args[position - 1])
        if not isinstance(arg, str):
            cleaned.append(arg)
        elif follows_secret_flag and not arg.startswith("-"):
            cleaned.append(SECRET_REMOVED)
        else:
            cleaned.append(without_text_credentials(arg))
    return cleaned


def has_missing_secrets(server: McpServerConfig) -> bool:
    blank_value = any(value == "" for value in (*server.env.values(), *server.headers.values()))
    return blank_value or any(SECRET_REMOVED in text for text in (*server.args, server.url))
