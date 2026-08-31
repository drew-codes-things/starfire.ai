"""Provider-aware chat streaming.

Each upstream provider streams a different wire format:
  - Ollama native /api/chat: newline-delimited JSON, {"message":{"content":...},"done":bool},
    with tool calls arriving whole (not incrementally) on the final line as
    message.tool_calls: [{"function":{"name":..., "arguments": {...}}}].
  - Anthropic /v1/messages (stream=true): SSE events. Text deltas at
    content_block_delta/text_delta; tool-call deltas start at
    content_block_start (content_block.type == "tool_use", carries id+name),
    accumulate as input_json_delta chunks on content_block_delta, and close
    at content_block_stop.
  - OpenAI-compatible /chat/completions (stream=true): SSE "data: {...}" lines,
    text delta at choices[0].delta.content, tool-call deltas at
    choices[0].delta.tool_calls[i] (accumulated by index across chunks since
    id/name/arguments arrive split across multiple chunks), terminated by
    "data: [DONE]".

stream_chat_once() is the shared low-level layer: one async generator per
provider, all normalized to the same tuple stream regardless of upstream
format:
    ("delta", "text chunk")
    ("tool_calls", [{"id":..., "name":..., "arguments": {...}}, ...])
    ("error", "message")

agent_loop.py drives this directly (to run the tool-calling round loop).
stream_chat() below is the plain, no-tools path used by the existing
/api/chat_stream route when there are no MCP servers enabled — it wraps
stream_chat_once() and keeps its original SSE-framed-bytes contract
unchanged, so chat behavior with tools disabled is unaffected by this file's
refactor.
"""

import json

import httpx

from providers import _detect_provider, build_chat_url, build_headers, build_tools_param


def _sse_line(payload: dict) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


def _build_messages(messages: list[dict], system: str | None) -> list[dict]:
    return ([{"role": "system", "content": system}] + messages) if system else messages


def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Translate the OpenAI-shaped tool-calling history agent_loop.py builds
    (assistant messages carrying a 'tool_calls' list, role:'tool' result
    messages) into Anthropic's shape, which has neither: tool calls are
    content blocks on a normal assistant message, and results come back as a
    role:'user' message with a 'tool_result' content block. Anthropic's API
    rejects an unrecognized role/field outright, so a plain-chat message list
    (no tool_calls anywhere) must still pass through unchanged."""
    out = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": m.get("tool_call_id"),
                             "content": m.get("content", "")}],
            })
        elif role == "assistant" and m.get("tool_calls"):
            content = []
            if m.get("content"):
                content.append({"type": "text", "text": m["content"]})
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                content.append({"type": "tool_use", "id": tc.get("id"),
                                 "name": fn.get("name"), "input": args or {}})
            out.append({"role": "assistant", "content": content})
        else:
            out.append({"role": role, "content": m.get("content", "")})
    return out


def _to_ollama_messages(messages: list[dict]) -> list[dict]:
    """Ollama's native /api/chat wants tool_calls.function.arguments as a
    plain dict, not the JSON-string-encoded form OpenAI's wire format (and
    agent_loop.py's working-message history) uses — unstring it back. The
    role:'tool' result messages Ollama expects are otherwise identical to the
    OpenAI shape, so those pass through unchanged."""
    out = []
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                calls.append({"function": {"name": fn.get("name"), "arguments": args or {}}})
            out.append({"role": "assistant", "content": m.get("content", ""), "tool_calls": calls})
        else:
            out.append(m)
    return out


async def _iter_ollama(client: httpx.AsyncClient, url: str, headers: dict, payload: dict):
    async with client.stream("POST", url, headers=headers, json=payload) as r:
        if r.is_error:
            body = await r.aread()
            yield ("error", f"Ollama {r.status_code}: {body.decode(errors='replace')[:500]}")
            return
        async for line in r.aiter_lines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = obj.get("message") or {}
            content = message.get("content")
            if content:
                yield ("delta", content)
            raw_calls = message.get("tool_calls")
            if raw_calls:
                calls = []
                for i, c in enumerate(raw_calls):
                    fn = c.get("function", {})
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    calls.append({"id": c.get("id") or f"ollama-{i}", "name": fn.get("name"),
                                  "arguments": args or {}})
                yield ("tool_calls", calls)
            if obj.get("done"):
                return


async def _iter_anthropic(client: httpx.AsyncClient, url: str, headers: dict, payload: dict):
    async with client.stream("POST", url, headers=headers, json=payload) as r:
        if r.is_error:
            body = await r.aread()
            yield ("error", f"Anthropic {r.status_code}: {body.decode(errors='replace')[:500]}")
            return

        event_type = None
        tool_blocks: dict[int, dict] = {}  # index -> {id, name, json_buf}

        async for raw in r.aiter_lines():
            line = raw.strip()
            if not line:
                event_type = None
                continue
            if line.startswith("event:"):
                event_type = line[len("event:"):].strip()
                continue
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue

            if event_type == "content_block_start":
                block = obj.get("content_block") or {}
                if block.get("type") == "tool_use":
                    tool_blocks[obj.get("index", 0)] = {
                        "id": block.get("id"), "name": block.get("name"), "json_buf": "",
                    }
            elif event_type == "content_block_delta":
                delta = obj.get("delta") or {}
                idx = obj.get("index", 0)
                if delta.get("type") == "text_delta" and delta.get("text"):
                    yield ("delta", delta["text"])
                elif delta.get("type") == "input_json_delta" and idx in tool_blocks:
                    tool_blocks[idx]["json_buf"] += delta.get("partial_json", "")
            elif event_type == "error":
                message = (obj.get("error") or {}).get("message") or "Anthropic stream error"
                yield ("error", message)
                return
            elif event_type == "message_delta":
                if (obj.get("delta") or {}).get("stop_reason") == "tool_use" and tool_blocks:
                    calls = []
                    for block in tool_blocks.values():
                        try:
                            args = json.loads(block["json_buf"]) if block["json_buf"] else {}
                        except json.JSONDecodeError:
                            args = {}
                        calls.append({"id": block["id"], "name": block["name"], "arguments": args})
                    yield ("tool_calls", calls)
            elif event_type == "message_stop":
                return


async def _iter_openai(client: httpx.AsyncClient, url: str, headers: dict, payload: dict):
    async with client.stream("POST", url, headers=headers, json=payload) as r:
        if r.is_error:
            body = await r.aread()
            yield ("error", f"{r.status_code}: {body.decode(errors='replace')[:500]}")
            return

        call_acc: dict[int, dict] = {}  # index -> {id, name, args_buf}
        finish_reason = None

        async for raw in r.aiter_lines():
            line = raw.strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = obj.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if content:
                yield ("delta", content)
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = call_acc.setdefault(idx, {"id": None, "name": None, "args_buf": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["args_buf"] += fn["arguments"]
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]

        if finish_reason == "tool_calls" and call_acc:
            calls = []
            for i, slot in sorted(call_acc.items()):
                try:
                    args = json.loads(slot["args_buf"]) if slot["args_buf"] else {}
                except json.JSONDecodeError:
                    args = {}
                calls.append({"id": slot["id"] or f"call-{i}", "name": slot["name"], "arguments": args})
            yield ("tool_calls", calls)


async def stream_chat_once(base_url: str, api_key: str | None, model: str, messages: list[dict],
                            system: str | None, options: dict | None, tools: list[dict] | None = None):
    """One round of provider streaming, normalized to (kind, payload) tuples.
    Does not itself decide when a "turn" is over — the caller (stream_chat's
    SSE wrapper, or agent_loop's round loop) does."""
    provider = _detect_provider(base_url)
    url = build_chat_url(base_url)
    headers = build_headers(api_key, base_url)
    tools_param = build_tools_param(tools or [], base_url)

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        try:
            if provider == "ollama":
                payload = {
                    "model": model, "stream": True, "keep_alive": -1,
                    "messages": _to_ollama_messages(_build_messages(messages, system)),
                    **({"options": options} if options else {}),
                    **({"tools": tools_param} if tools_param else {}),
                }
                async for item in _iter_ollama(client, url, headers, payload):
                    yield item
            elif provider == "anthropic":
                payload = {
                    "model": model, "stream": True,
                    "max_tokens": (options or {}).get("max_tokens", 4096),
                    "messages": _to_anthropic_messages(messages),
                    **({"system": system} if system else {}),
                    **({"tools": tools_param} if tools_param else {}),
                }
                async for item in _iter_anthropic(client, url, headers, payload):
                    yield item
            else:
                payload = {
                    "model": model, "stream": True,
                    "messages": _build_messages(messages, system),
                    **({"tools": tools_param} if tools_param else {}),
                }
                async for item in _iter_openai(client, url, headers, payload):
                    yield item
        except httpx.HTTPError as e:
            yield ("error", str(e))


async def stream_chat(base_url: str, api_key: str | None, model: str, messages: list[dict],
                       system: str | None, options: dict | None):
    """SSE-framed-bytes wrapper over stream_chat_once, no tools — this is the
    unchanged public contract routes.py used before tool-calling existed, and
    still uses when no MCP servers are enabled for a request."""
    async for kind, payload in stream_chat_once(base_url, api_key, model, messages, system, options):
        if kind == "delta":
            yield _sse_line({"delta": payload})
        elif kind == "error":
            yield _sse_line({"error": payload})
            return
        # ("tool_calls", ...) is ignored here — a model that emits a tool call
        # with no tools advertised is unusual, and this path has nothing to
        # execute it with; treat it as if the round simply produced no text.
    yield _sse_line({"done": True})
