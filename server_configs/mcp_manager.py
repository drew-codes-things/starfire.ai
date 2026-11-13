import asyncio
import logging
from contextlib import AsyncExitStack

import anyio
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from mcp_servers_store import with_url_credentials_hidden, without_url_credentials

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 45
TOOL_CALL_TIMEOUT_S = 60
REMOTE_CLOSE_TIMEOUT_S = 10
REMOTE_TRANSPORTS = ("http", "sse")
SIGNED_IN_TRANSPORT = "http"
JSONRPC_CONNECTION_CLOSED = -32000
CONNECTION_CLOSED_MESSAGE = "Connection closed"
CLOSED_STREAM_ERRORS = (anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream)


class McpConnectionError(Exception):
    pass


def _root_cause(error: BaseException) -> str:
    seen = error
    while isinstance(seen, BaseExceptionGroup) and seen.exceptions:
        seen = seen.exceptions[0]
    return str(seen) or type(seen).__name__


def _error_code(error: BaseException):
    code = getattr(error, "code", None)
    return code if code is not None else getattr(getattr(error, "error", None), "code", None)


def _is_closed_connection(error: BaseException) -> bool:
    if isinstance(error, CLOSED_STREAM_ERRORS):
        return True
    message = getattr(getattr(error, "error", None), "message", None) or str(error)
    return _error_code(error) == JSONRPC_CONNECTION_CLOSED and message == CONNECTION_CLOSED_MESSAGE


def _tool_list(result) -> list[dict]:
    return [
        {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
        for t in result.tools
    ]


class McpManager:
    def __init__(self):
        self._sessions: dict[str, ClientSession] = {}
        self._exit_stacks: dict[str, AsyncExitStack] = {}
        self._remote_tasks: dict[str, tuple[asyncio.Task, asyncio.Event]] = {}
        self._tools_cache: dict[str, list[dict]] = {}
        self._lock = asyncio.Lock()

    async def connect_server(self, server, auth: httpx.Auth | None = None) -> list[dict]:
        return await self.connect(server.id, server.command, server.args, server.env or None,
                                  transport=getattr(server, "transport", "stdio") or "stdio",
                                  url=getattr(server, "url", "") or "",
                                  headers=getattr(server, "headers", None) or None, auth=auth)

    async def connect(self, server_id: str, command: str, args: list[str],
                       env: dict[str, str] | None = None, transport: str = "stdio", url: str = "",
                       headers: dict[str, str] | None = None, auth: httpx.Auth | None = None) -> list[dict]:
        async with self._lock:
            self._forget_ended_sessions()
            if server_id in self._sessions:
                return self._tools_cache.get(server_id, [])
            if auth is not None and transport != SIGNED_IN_TRANSPORT:
                raise McpConnectionError("browser sign-in works with streamable HTTP MCP servers only")
            if transport in REMOTE_TRANSPORTS:
                session, tools = await self._connect_remote(server_id, transport, url, headers, auth)
            else:
                session, tools = await self._connect_stdio(server_id, command, args, env)
            self._sessions[server_id] = session
            self._tools_cache[server_id] = tools
            return tools

    async def _connect_stdio(self, server_id: str, command: str, args: list[str],
                             env: dict[str, str] | None) -> tuple[ClientSession, list[dict]]:
        stack = AsyncExitStack()
        try:
            params = StdioServerParameters(command=command, args=args, env=env)
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await asyncio.wait_for(session.initialize(), timeout=CONNECT_TIMEOUT_S)
            tools = _tool_list(await asyncio.wait_for(session.list_tools(), timeout=CONNECT_TIMEOUT_S))
        except Exception as e:
            try:
                await stack.aclose()
            except Exception as close_error:
                logger.debug("ignoring error while closing failed MCP connection: %s", close_error)
            raise McpConnectionError(f"failed to start '{command}': {_root_cause(e)}") from e
        self._exit_stacks[server_id] = stack
        return session, tools

    async def _connect_remote(self, server_id: str, transport: str, url: str, headers: dict[str, str] | None,
                              auth: httpx.Auth | None) -> tuple[ClientSession, list[dict]]:
        if not url:
            raise McpConnectionError(f"an {transport.upper()} MCP server needs a URL")
        ready: asyncio.Future = asyncio.get_running_loop().create_future()
        stop = asyncio.Event()
        task = asyncio.create_task(self._serve_remote(transport, url, headers, auth, ready, stop))
        try:
            session, tools = await asyncio.wait_for(ready, timeout=CONNECT_TIMEOUT_S + 5)
        except asyncio.TimeoutError as e:
            stop.set()
            task.cancel()
            raise McpConnectionError(f"failed to connect to '{without_url_credentials(url)}': timed out") from e
        except Exception as e:
            stop.set()
            raise McpConnectionError(f"failed to connect to '{without_url_credentials(url)}': "
                                     f"{with_url_credentials_hidden(_root_cause(e))}") from e
        self._remote_tasks[server_id] = (task, stop)
        return session, tools

    async def _serve_remote(self, transport: str, url: str, headers: dict[str, str] | None, auth: httpx.Auth | None,
                            ready: asyncio.Future, stop: asyncio.Event) -> None:
        try:
            async with AsyncExitStack() as stack:
                if transport == "http":
                    client = httpx.AsyncClient(headers=headers or {}, auth=auth, follow_redirects=True,
                                               timeout=httpx.Timeout(30.0, read=300.0))
                    await stack.enter_async_context(client)
                    streams = await stack.enter_async_context(streamable_http_client(url, http_client=client))
                else:
                    streams = await stack.enter_async_context(sse_client(url, headers=headers or None))
                session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
                await asyncio.wait_for(session.initialize(), timeout=CONNECT_TIMEOUT_S)
                tools = _tool_list(await asyncio.wait_for(session.list_tools(), timeout=CONNECT_TIMEOUT_S))
                if not ready.done():
                    ready.set_result((session, tools))
                await stop.wait()
        except BaseException as e:
            if not ready.done():
                ready.set_exception(e if isinstance(e, Exception) else McpConnectionError(_root_cause(e)))
            elif not isinstance(e, asyncio.CancelledError):
                logger.warning("remote MCP server at %s closed: %s", without_url_credentials(url),
                               with_url_credentials_hidden(_root_cause(e)))

    def _pop_entries(self, server_id: str):
        stack = self._exit_stacks.pop(server_id, None)
        remote = self._remote_tasks.pop(server_id, None)
        self._sessions.pop(server_id, None)
        self._tools_cache.pop(server_id, None)
        return stack, remote

    def _forget_ended_sessions(self) -> None:
        ended = [server_id for server_id, (task, _stop) in self._remote_tasks.items() if task.done()]
        for server_id in ended:
            logger.info("remote MCP server %s is no longer connected, it reconnects when next needed", server_id)
            self._pop_entries(server_id)

    async def _drop_closed_session(self, server_id: str, session: ClientSession) -> None:
        async with self._lock:
            if self._sessions.get(server_id) is not session:
                return
            stack, remote = self._pop_entries(server_id)
        logger.info("MCP server %s closed its connection, it reconnects when next needed", server_id)
        await self._close(server_id, stack, remote)

    async def disconnect(self, server_id: str) -> None:
        async with self._lock:
            stack, remote = self._pop_entries(server_id)
        await self._close(server_id, stack, remote)

    async def _close(self, server_id: str, stack: AsyncExitStack | None, remote) -> None:
        if stack:
            try:
                await stack.aclose()
            except Exception as e:
                logger.warning("error closing MCP server %s: %s", server_id, e)
        if remote:
            task, stop = remote
            stop.set()
            try:
                await asyncio.wait_for(task, timeout=REMOTE_CLOSE_TIMEOUT_S)
            except (asyncio.TimeoutError, Exception) as e:
                task.cancel()
                logger.warning("error closing remote MCP server %s: %s", server_id, _root_cause(e))

    def is_connected(self, server_id: str) -> bool:
        self._forget_ended_sessions()
        return server_id in self._sessions

    def list_tools(self, server_id: str) -> list[dict]:
        self._forget_ended_sessions()
        return self._tools_cache.get(server_id, [])

    def get_all_openai_schemas(self, enabled_ids: set[str]) -> list[dict]:
        self._forget_ended_sessions()
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

        self._forget_ended_sessions()
        session = self._sessions.get(server_id)
        if not session:
            raise McpConnectionError(f"MCP server not connected: {server_id}")

        try:
            result = await asyncio.wait_for(session.call_tool(tool_name, arguments), timeout=TOOL_CALL_TIMEOUT_S)
        except Exception as error:
            if not _is_closed_connection(error):
                raise
            await self._drop_closed_session(server_id, session)
            raise McpConnectionError(f"MCP server {server_id} closed its connection; "
                                     "it reconnects on the next chat") from error
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
