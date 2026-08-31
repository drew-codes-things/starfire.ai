"""MCP client — connects out to stdio Model Context Protocol servers and
exposes their tools to the chat/tool-calling loop.

Mirrors odysseus-dev's src/mcp_manager.py client role (ClientSession over
stdio_client), trimmed to stdio transport only — no SSE/HTTP/OAuth, which
odysseus needs for remote/authenticated MCP servers but starfire's scope
(the 3 official reference servers + arbitrary local commands) doesn't.

Connections are long-lived subprocesses kept open for the app's lifetime,
same as odysseus's builtin-server model — not spawned per request.
"""

import asyncio
import logging
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


class McpConnectionError(Exception):
    pass


class McpManager:
    def __init__(self):
        self._sessions: dict[str, ClientSession] = {}
        self._exit_stacks: dict[str, AsyncExitStack] = {}
        self._tools_cache: dict[str, list[dict]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, server_id: str, command: str, args: list[str],
                       env: dict[str, str] | None = None) -> list[dict]:
        """Start the server subprocess, initialize the session, and cache its
        tool list. Returns the tool list (as plain dicts) on success; raises
        McpConnectionError with a readable message on failure — the same
        contract /api/model-endpoints/test relies on for its probe-before-add
        UX, reused here for the "quick add" flow."""
        async with self._lock:
            if server_id in self._sessions:
                return self._tools_cache.get(server_id, [])

            stack = AsyncExitStack()
            try:
                params = StdioServerParameters(command=command, args=args, env=env)
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                # 45s, not 15s: an npx/uvx package that isn't cached yet has
                # to be fetched over the network before the server even
                # starts, on top of MCP's own initialize handshake.
                await asyncio.wait_for(session.initialize(), timeout=45)
                result = await asyncio.wait_for(session.list_tools(), timeout=45)
                tools = [
                    {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
                    for t in result.tools
                ]
            except Exception as e:
                # Must close within this same task — stdio_client's anyio
                # task group raises "cancel scope in a different task" if
                # torn down later (e.g. by GC after the exception escaped
                # uncleaned), so every failure path after entering the stack
                # closes it right here, not just the connect/initialize ones.
                await stack.aclose()
                # str(TimeoutError()) is '' — name the exception type too so
                # a timeout doesn't surface as a blank, useless error message.
                detail = str(e) or type(e).__name__
                raise McpConnectionError(f"failed to start '{command}': {detail}") from e

            self._sessions[server_id] = session
            self._exit_stacks[server_id] = stack
            self._tools_cache[server_id] = tools
            return tools

    async def disconnect(self, server_id: str) -> None:
        async with self._lock:
            stack = self._exit_stacks.pop(server_id, None)
            self._sessions.pop(server_id, None)
            self._tools_cache.pop(server_id, None)
        if stack:
            try:
                await stack.aclose()
            except Exception as e:
                logger.warning("error closing MCP server %s: %s", server_id, e)

    def is_connected(self, server_id: str) -> bool:
        return server_id in self._sessions

    def list_tools(self, server_id: str) -> list[dict]:
        return self._tools_cache.get(server_id, [])

    def get_all_openai_schemas(self, enabled_ids: set[str]) -> list[dict]:
        """OpenAI-style function-tool schemas across every connected+enabled
        server, named mcp__{server_id}__{tool_name} — same qualification
        scheme as odysseus's McpManager.get_all_openai_schemas, so a tool
        call can be routed back to the right server/tool by splitting the
        name."""
        schemas = []
        for server_id, tools in self._tools_cache.items():
            if server_id not in enabled_ids:
                continue
            for tool in tools:
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": f"mcp__{server_id}__{tool['name']}",
                        "description": tool["description"],
                        "parameters": tool["input_schema"] or {"type": "object", "properties": {}},
                    },
                })
        return schemas

    async def call_tool(self, qualified_name: str, arguments: dict) -> str:
        """Split 'mcp__{server_id}__{tool_name}', dispatch to that server's
        session, and stringify the result content for feeding back into the
        conversation as a role:"tool" message."""
        if not qualified_name.startswith("mcp__"):
            raise McpConnectionError(f"not an MCP tool: {qualified_name}")
        try:
            _, server_id, tool_name = qualified_name.split("__", 2)
        except ValueError:
            raise McpConnectionError(f"malformed MCP tool name: {qualified_name}")

        session = self._sessions.get(server_id)
        if not session:
            raise McpConnectionError(f"MCP server not connected: {server_id}")

        result = await asyncio.wait_for(session.call_tool(tool_name, arguments), timeout=60)
        parts = []
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        text = "\n".join(parts) or "(tool returned no content)"
        if getattr(result, "isError", False):
            text = f"error: {text}"
        return text


mcp_manager = McpManager()
