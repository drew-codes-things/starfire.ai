import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import pytest
from mcp import MCPError

import mcp_manager
from mcp_manager import McpConnectionError, McpManager

QUERY_KEY = "PLACEHOLDER-query-key"
URL_PASSWORD = "PLACEHOLDER-url-password"
SECRET_URL = f"https://reader:{URL_PASSWORD}@mcp.example.test/sse?api_key={QUERY_KEY}"


def test_a_failed_connection_names_the_server_without_its_url_credentials(monkeypatch):
    @asynccontextmanager
    async def refusing_sse_client(url, headers=None):
        raise RuntimeError(f"Client error '401 Unauthorized' for url '{url}'")
        yield

    monkeypatch.setattr(mcp_manager, "sse_client", refusing_sse_client)
    with pytest.raises(McpConnectionError) as raised:
        asyncio.run(McpManager().connect("remote1", "", [], transport="sse", url=SECRET_URL))
    message = str(raised.value)
    assert "mcp.example.test/sse" in message and "401 Unauthorized" in message
    assert QUERY_KEY not in message and URL_PASSWORD not in message


def test_a_dropped_connection_is_logged_without_its_url_credentials(monkeypatch, caplog):
    class FakeSession:
        def __init__(self, read, write):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def initialize(self):
            return None

        async def list_tools(self):
            return type("Listed", (), {"tools": []})()

    @asynccontextmanager
    async def dropping_sse_client(url, headers=None):
        yield (None, None)
        raise RuntimeError(f"stream from {url} ended")

    monkeypatch.setattr(mcp_manager, "sse_client", dropping_sse_client)
    monkeypatch.setattr(mcp_manager, "ClientSession", FakeSession)

    async def connect_then_drop():
        manager = McpManager()
        await manager.connect("remote1", "", [], transport="sse", url=SECRET_URL)
        await manager.disconnect("remote1")

    with caplog.at_level(logging.WARNING, logger="mcp_manager"):
        asyncio.run(connect_then_drop())
    assert "mcp.example.test/sse" in caplog.text
    assert QUERY_KEY not in caplog.text and URL_PASSWORD not in caplog.text


SDK_CONNECTION_CLOSED = -32000


class ClosedSession:
    async def call_tool(self, name, arguments):
        raise MCPError(code=SDK_CONNECTION_CLOSED, message="Connection closed")


class RemoteToolError(Exception):
    code = SDK_CONNECTION_CLOSED


class FailingToolSession:
    async def call_tool(self, name, arguments):
        raise RemoteToolError("the tool itself failed")


def test_a_call_that_hits_a_closed_connection_drops_the_session_so_it_can_reconnect():
    async def scenario():
        manager = McpManager()
        manager._sessions["gone"] = ClosedSession()
        manager._tools_cache["gone"] = [{"name": "echo", "description": "", "input_schema": None}]
        with pytest.raises(McpConnectionError, match="closed its connection"):
            await manager.call_tool("mcp__gone__echo", {})
        return manager.is_connected("gone"), manager.get_all_openai_schemas({"gone"})

    assert asyncio.run(scenario()) == (False, [])


def test_a_tool_error_that_is_not_a_closed_connection_keeps_the_session():
    async def scenario():
        manager = McpManager()
        manager._sessions["busy"] = FailingToolSession()
        with pytest.raises(RemoteToolError):
            await manager.call_tool("mcp__busy__echo", {})
        return manager.is_connected("busy")

    assert asyncio.run(scenario()) is True


def test_a_remote_session_whose_transport_ended_is_no_longer_connected():
    async def scenario():
        manager = McpManager()
        ended = asyncio.create_task(asyncio.sleep(0))
        await ended
        manager._sessions["remote"] = object()
        manager._tools_cache["remote"] = [{"name": "echo", "description": "", "input_schema": None}]
        manager._remote_tasks["remote"] = (ended, asyncio.Event())
        return manager.is_connected("remote"), manager.list_tools("remote")

    assert asyncio.run(scenario()) == (False, [])
