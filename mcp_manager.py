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
        async with self._lock:
            if server_id in self._sessions:
                return self._tools_cache.get(server_id, [])

            stack = AsyncExitStack()
            try:
                params = StdioServerParameters(command=command, args=args, env=env)
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await asyncio.wait_for(session.initialize(), timeout=45)
                result = await asyncio.wait_for(session.list_tools(), timeout=45)
                tools = [
                    {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
                    for t in result.tools
                ]
            except Exception as e:
                await stack.aclose()
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
