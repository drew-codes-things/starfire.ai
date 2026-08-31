"""MCP server registry: a JSON file, same shape/philosophy as model_endpoints.py
(no DB — odysseus uses a DB-backed McpServer table because it's multi-user;
starfire is single-user and already committed to file-based config elsewhere)."""

# Deferred annotation evaluation: McpServerStore defines a method named
# list(), which would otherwise shadow the builtin `list` for every type hint
# written after it in the class body (e.g. `args: list[str]` in add()).
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field

from atomic_io import atomic_write_json


@dataclass
class McpServerConfig:
    id: str
    name: str
    command: str
    args: list[str]
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


# One-click presets for the Tools settings tab. Verified against the official
# MCP reference servers repo (github.com/modelcontextprotocol/servers):
# filesystem/memory are npm packages run via npx, fetch is a Python package
# run via uvx (not npm — an initial lookup got this wrong before checking the
# server's own README).
REFERENCE_SERVERS = {
    "filesystem": {
        "name": "Filesystem",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem"],
        "needs_path": True,  # UI collects one allowed directory, appended to args
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

# A broader catalog beyond the three always-available quick-adds above —
# each of these needs a "configure" step (an API key/token, typically)
# before it can connect, unlike the zero-config REFERENCE_SERVERS. Based on
# the official MCP reference servers repo and well-known community servers;
# package names/availability can drift over time in an ecosystem this
# young, so treat this list as a starting point, not a guarantee. That's a
# safe thing to ship regardless: add_mcp_server already validates every
# addition by actually connecting before persisting it (see routes.py), so
# a stale entry here just fails with a clear connection error — it can
# never silently break anything already configured.
MCP_SERVER_REPOSITORY = {
    "git": {
        "name": "Git",
        "command": "uvx",
        "args": ["mcp-server-git", "--repository"],
        "needs_path": True,  # path to a local git repository
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
        # mcp-server-sqlite is an old, unmaintained reference server written
        # against mcp SDK 1.0's Server API (Server.list_resources() etc.),
        # which the current SDK (2.x, what this app's own venv resolves)
        # removed — it crashes with a bare AttributeError against the
        # latest mcp otherwise. Verified directly: pinning the old SDK via
        # --with is what actually makes this specific server work.
        "args": ["--with", "mcp==1.0.0", "mcp-server-sqlite", "--db-path"],
        "needs_path": True,  # path to a .db file
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

    def _load(self) -> list[McpServerConfig]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        servers = []
        for item in raw:
            if not isinstance(item, dict) or "id" not in item or "command" not in item:
                continue
            servers.append(McpServerConfig(
                id=item["id"],
                name=item.get("name", item["command"]),
                command=item["command"],
                args=item.get("args", []),
                env=item.get("env", {}),
                enabled=item.get("enabled", True),
            ))
        return servers

    def _save(self, servers: list[McpServerConfig]) -> None:
        atomic_write_json(self.path, [asdict(s) for s in servers])

    def list(self) -> list[McpServerConfig]:
        return self._load()

    def get(self, server_id: str) -> McpServerConfig | None:
        for s in self._load():
            if s.id == server_id:
                return s
        return None

    def add(self, name: str, command: str, args: list[str], env: dict[str, str] | None = None) -> McpServerConfig:
        servers = self._load()
        server = McpServerConfig(id=uuid.uuid4().hex[:12], name=name, command=command,
                                  args=args, env=env or {})
        servers.append(server)
        self._save(servers)
        return server

    def set_enabled(self, server_id: str, enabled: bool) -> bool:
        servers = self._load()
        found = False
        for s in servers:
            if s.id == server_id:
                s.enabled = enabled
                found = True
        if found:
            self._save(servers)
        return found

    def delete(self, server_id: str) -> bool:
        servers = self._load()
        remaining = [s for s in servers if s.id != server_id]
        if len(remaining) == len(servers):
            return False
        self._save(remaining)
        return True
