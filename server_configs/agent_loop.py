import asyncio
import json

import builtin_tools
from chat_stream import _sse_line, stream_chat_once
from mcp_manager import mcp_manager

MAX_TOOL_ROUNDS = 5

async def run_chat_with_tools(base_url: str, api_key: str | None, model: str, messages: list[dict],
                               system: str | None, options: dict | None, enabled_server_ids: set[str],
                               enabled_builtin_tools: set[str], ctx: "builtin_tools.ToolContext",
                               max_tools: int | None = None):
    tools = builtin_tools.schemas_for(enabled_builtin_tools) + mcp_manager.get_all_openai_schemas(enabled_server_ids)
    if max_tools is not None:
        tools = tools[:max_tools]
    working_messages = list(messages)

    for _round in range(MAX_TOOL_ROUNDS):
        text = ""
        tool_calls: list[dict] = []
        had_error = False

        async for kind, payload in stream_chat_once(base_url, api_key, model, working_messages,
                                                      system, options, tools):
            if kind == "delta":
                text += payload
                yield _sse_line({"delta": payload})
            elif kind == "tool_calls":
                tool_calls = payload
            elif kind == "error":
                yield _sse_line({"error": payload})
                had_error = True

        if had_error:
            return

        if not tool_calls:
            yield _sse_line({"done": True})
            return

        working_messages.append({
            "role": "assistant",
            "content": text,
            "tool_calls": [
                {"id": c["id"], "type": "function",
                 "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                for c in tool_calls
            ],
        })

        for call in tool_calls:
            yield _sse_line({"tool_start": call["name"]})
            result = ""
            async for kind, payload in _execute_tool(call, ctx):
                if kind == "event":
                    yield _sse_line(payload)
                else:
                    result = payload
            yield _sse_line({"tool_result": {"name": call["name"], "result": result[:2000]}})
            working_messages.append({
                "role": "tool", "tool_call_id": call["id"], "content": result,
            })

    yield _sse_line({"error": "tool-call round limit reached"})

async def _call_tool(call: dict, ctx: "builtin_tools.ToolContext") -> str:
    try:
        if call["name"].startswith("mcp__"):
            return await mcp_manager.call_tool(call["name"], call["arguments"])
        return await builtin_tools.call_builtin_tool(call["name"], call["arguments"], ctx)
    except Exception as e:
        return f"error: {e}"

async def _execute_tool(call: dict, ctx: "builtin_tools.ToolContext"):
    events = getattr(ctx, "events", None)
    task = asyncio.ensure_future(_call_tool(call, ctx))
    if events is None:
        yield "result", await task
        return
    while True:
        getter = asyncio.ensure_future(events.get())
        done, _ = await asyncio.wait({task, getter}, return_when=asyncio.FIRST_COMPLETED)
        if getter in done:
            yield "event", getter.result()
            continue
        getter.cancel()
        while not events.empty():
            yield "event", events.get_nowait()
        yield "result", task.result()
        return

async def run_chat_collected(base_url: str, api_key: str | None, model: str, messages: list[dict],
                              system: str | None = None, options: dict | None = None,
                              enabled_server_ids: set[str] | None = None,
                              enabled_builtin_tools: set[str] | None = None,
                              ctx: "builtin_tools.ToolContext | None" = None,
                              think: bool | None = None, max_tools: int | None = None) -> str:
    enabled_server_ids = enabled_server_ids or set()
    enabled_builtin_tools = enabled_builtin_tools or set()

    text_parts: list[str] = []
    error: str | None = None

    if (enabled_server_ids or enabled_builtin_tools) and ctx is not None:
        async for chunk in run_chat_with_tools(base_url, api_key, model, messages, system, options,
                                                 enabled_server_ids, enabled_builtin_tools, ctx, max_tools=max_tools):
            for kind, payload in _parse_sse_bytes(chunk):
                if kind == "delta":
                    text_parts.append(payload)
                elif kind == "error":
                    error = payload
    else:
        async for kind, payload in stream_chat_once(base_url, api_key, model, messages, system, options,
                                                    think=think):
            if kind == "delta":
                text_parts.append(payload)
            elif kind == "error":
                error = payload

    if error:
        raise RuntimeError(error)
    return "".join(text_parts)

def _parse_sse_bytes(chunk: bytes):
    text = chunk.decode("utf-8", errors="replace").strip()
    if not text.startswith("data:"):
        return
    try:
        obj = json.loads(text[len("data:"):].strip())
    except json.JSONDecodeError:
        return
    if "delta" in obj:
        yield "delta", obj["delta"]
    elif "error" in obj:
        yield "error", obj["error"]
