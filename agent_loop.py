import json

import builtin_tools
from chat_stream import _sse_line, stream_chat_once
from mcp_manager import mcp_manager

MAX_TOOL_ROUNDS = 5

async def run_chat_with_tools(base_url: str, api_key: str | None, model: str, messages: list[dict],
                               system: str | None, options: dict | None, enabled_server_ids: set[str],
                               enabled_builtin_tools: set[str], ctx: "builtin_tools.ToolContext"):
    tools = mcp_manager.get_all_openai_schemas(enabled_server_ids) + builtin_tools.schemas_for(enabled_builtin_tools)
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
            try:
                if call["name"].startswith("mcp__"):
                    result = await mcp_manager.call_tool(call["name"], call["arguments"])
                else:
                    result = await builtin_tools.call_builtin_tool(call["name"], call["arguments"], ctx)
            except Exception as e:
                result = f"error: {e}"
            yield _sse_line({"tool_result": {"name": call["name"], "result": result[:2000]}})
            working_messages.append({
                "role": "tool", "tool_call_id": call["id"], "content": result,
            })

    yield _sse_line({"error": "tool-call round limit reached"})

async def run_chat_collected(base_url: str, api_key: str | None, model: str, messages: list[dict],
                              system: str | None = None, options: dict | None = None,
                              enabled_server_ids: set[str] | None = None,
                              enabled_builtin_tools: set[str] | None = None,
                              ctx: "builtin_tools.ToolContext | None" = None) -> str:
    enabled_server_ids = enabled_server_ids or set()
    enabled_builtin_tools = enabled_builtin_tools or set()

    text_parts: list[str] = []
    error: str | None = None

    if (enabled_server_ids or enabled_builtin_tools) and ctx is not None:
        async for chunk in run_chat_with_tools(base_url, api_key, model, messages, system, options,
                                                 enabled_server_ids, enabled_builtin_tools, ctx):
            for kind, payload in _parse_sse_bytes(chunk):
                if kind == "delta":
                    text_parts.append(payload)
                elif kind == "error":
                    error = payload
    else:
        async for kind, payload in stream_chat_once(base_url, api_key, model, messages, system, options):
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
