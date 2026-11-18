import json

import httpx

from ollama_runner import for_runner
from providers import _detect_provider, build_chat_url, build_headers, build_tools_param

MAX_ERROR_CHARS = 500
NO_ERROR_DETAILS = "the model server gave no details"
NANOSECONDS_PER_SECOND = 1_000_000_000
MILLISECONDS_PER_SECOND = 1000
STREAM_OPTIONS_FIELD = "stream_options"
USAGE_IN_STREAM = {"include_usage": True}

def _sse_line(payload: dict) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()

def _token_count(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(value)

def _seconds(value, units_per_second: int) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return value / units_per_second

def _usage(prompt_tokens=None, completion_tokens=None, cached_prompt_tokens=None,
           generation_seconds: float | None = None, context_window=None) -> dict | None:
    counts = {"prompt_tokens": _token_count(prompt_tokens), "completion_tokens": _token_count(completion_tokens),
              "cached_prompt_tokens": _token_count(cached_prompt_tokens),
              "context_window": _token_count(context_window) or None}
    reported = {name: value for name, value in counts.items() if value is not None}
    if "prompt_tokens" not in reported and "completion_tokens" not in reported:
        return None
    if generation_seconds and reported.get("completion_tokens"):
        reported["generation_seconds"] = round(generation_seconds, 4)
    return reported

def _ollama_usage(done: dict, payload: dict) -> dict | None:
    return _usage(prompt_tokens=done.get("prompt_eval_count"), completion_tokens=done.get("eval_count"),
                  cached_prompt_tokens=done.get("prompt_eval_cached_count"),
                  generation_seconds=_seconds(done.get("eval_duration"), NANOSECONDS_PER_SECOND),
                  context_window=(payload.get("options") or {}).get("num_ctx"))

def _openai_usage(usage, timings) -> dict | None:
    usage = usage if isinstance(usage, dict) else {}
    timings = timings if isinstance(timings, dict) else {}
    details = usage.get("prompt_tokens_details")
    prompt_tokens = usage.get("prompt_tokens")
    cached_prompt_tokens = details.get("cached_tokens") if isinstance(details, dict) else None
    if _token_count(timings.get("prompt_n")) is not None:
        cached_prompt_tokens = _token_count(timings.get("cache_n"))
        prompt_tokens = timings["prompt_n"] + (cached_prompt_tokens or 0)
    return _usage(prompt_tokens=prompt_tokens,
                  completion_tokens=usage.get("completion_tokens", timings.get("predicted_n")),
                  cached_prompt_tokens=cached_prompt_tokens,
                  generation_seconds=_seconds(timings.get("predicted_ms"), MILLISECONDS_PER_SECOND))

def _anthropic_usage(started: dict, output_tokens) -> dict | None:
    written = _token_count(started.get("cache_creation_input_tokens")) or 0
    cached = _token_count(started.get("cache_read_input_tokens"))
    fresh = _token_count(started.get("input_tokens"))
    prompt_tokens = None if fresh is None else fresh + written + (cached or 0)
    return _usage(prompt_tokens=prompt_tokens, completion_tokens=output_tokens, cached_prompt_tokens=cached)

def _build_messages(messages: list[dict], system: str | None) -> list[dict]:
    return ([{"role": "system", "content": system}] + messages) if system else messages

def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
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
            yield ("error", f"Ollama {r.status_code}: {_refusal_text(body)}")
            return
        async for line in r.aiter_lines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("error"):
                yield ("error", f"Ollama: {obj['error']}")
                return
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
                usage = _ollama_usage(obj, payload)
                if usage:
                    yield ("usage", usage)
                return

async def _iter_anthropic(client: httpx.AsyncClient, url: str, headers: dict, payload: dict):
    async with client.stream("POST", url, headers=headers, json=payload) as r:
        if r.is_error:
            body = await r.aread()
            yield ("error", f"Anthropic {r.status_code}: {_refusal_text(body)}")
            return

        event_type = None
        tool_blocks: dict[int, dict] = {}
        prompt_usage: dict = {}
        output_tokens = None

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

            if event_type == "message_start":
                started = (obj.get("message") or {}).get("usage")
                prompt_usage = started if isinstance(started, dict) else {}
                output_tokens = prompt_usage.get("output_tokens", output_tokens)
            elif event_type == "content_block_start":
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
                output_tokens = (obj.get("usage") or {}).get("output_tokens", output_tokens)
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
                break

        usage = _anthropic_usage(prompt_usage, output_tokens)
        if usage:
            yield ("usage", usage)

def _error_message(error) -> str:
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error)

def _refusal_text(body: bytes) -> str:
    text = body.decode(errors="replace").strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict) and parsed.get("error"):
        text = _error_message(parsed["error"])
    return text[:MAX_ERROR_CHARS] or NO_ERROR_DETAILS

async def _iter_openai(client: httpx.AsyncClient, url: str, headers: dict, payload: dict):
    async with client.stream("POST", url, headers=headers, json=payload) as r:
        if r.is_error:
            body = await r.aread()
            yield ("error", f"{r.status_code}: {_refusal_text(body)}")
            return

        call_acc: dict[int, dict] = {}
        finish_reason = None
        reported_usage = None
        reported_timings = None

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
            if obj.get("error"):
                yield ("error", _error_message(obj["error"]))
                return
            reported_usage = obj.get("usage") or reported_usage
            reported_timings = obj.get("timings") or reported_timings
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

        usage = _openai_usage(reported_usage, reported_timings)
        if usage:
            yield ("usage", usage)

async def _iter_openai_asking_for_usage(client: httpx.AsyncClient, url: str, headers: dict, payload: dict):
    refused_the_usage_option = False
    said_anything = False
    async for kind, item in _iter_openai(client, url, headers, {**payload, STREAM_OPTIONS_FIELD: USAGE_IN_STREAM}):
        if kind == "error" and not said_anything and STREAM_OPTIONS_FIELD in item:
            refused_the_usage_option = True
            continue
        said_anything = True
        yield kind, item
    if refused_the_usage_option:
        async for each in _iter_openai(client, url, headers, payload):
            yield each

_HOSTED_PARAM_NAMES = {
    "anthropic": {"temperature": "temperature", "top_p": "top_p", "top_k": "top_k"},
    "openai": {"temperature": "temperature", "top_p": "top_p", "seed": "seed", "num_predict": "max_tokens"},
}

def _hosted_params(provider: str, options: dict | None) -> dict:
    names = _HOSTED_PARAM_NAMES.get(provider, _HOSTED_PARAM_NAMES["openai"])
    return {names[key]: value for key, value in (options or {}).items() if key in names and value is not None}

async def stream_chat_once(base_url: str, api_key: str | None, model: str, messages: list[dict],
                            system: str | None, options: dict | None, tools: list[dict] | None = None,
                            think: bool | None = None):
    provider = _detect_provider(base_url)
    url = build_chat_url(base_url)
    headers = build_headers(api_key, base_url)
    tools_param = build_tools_param(tools or [], base_url)

    async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
        try:
            if provider == "ollama":
                payload = for_runner({
                    "model": model, "stream": True, "keep_alive": -1,
                    "messages": _to_ollama_messages(_build_messages(messages, system)),
                    **({"options": options} if options else {}),
                    **({"tools": tools_param} if tools_param else {}),
                    **({"think": think} if think is not None else {}),
                })
                async for item in _iter_ollama(client, url, headers, payload):
                    yield item
            elif provider == "anthropic":
                payload = {
                    "model": model, "stream": True,
                    "max_tokens": (options or {}).get("num_predict") or (options or {}).get("max_tokens", 4096),
                    "messages": _to_anthropic_messages(messages),
                    **_hosted_params("anthropic", options),
                    **({"system": system} if system else {}),
                    **({"tools": tools_param} if tools_param else {}),
                }
                async for item in _iter_anthropic(client, url, headers, payload):
                    yield item
            else:
                payload = {
                    "model": model, "stream": True,
                    "messages": _build_messages(messages, system),
                    **_hosted_params("openai", options),
                    **({"tools": tools_param} if tools_param else {}),
                }
                async for item in _iter_openai_asking_for_usage(client, url, headers, payload):
                    yield item
        except httpx.HTTPError as e:
            yield ("error", str(e))

async def stream_chat(base_url: str, api_key: str | None, model: str, messages: list[dict],
                       system: str | None, options: dict | None):
    async for kind, payload in stream_chat_once(base_url, api_key, model, messages, system, options):
        if kind == "delta":
            yield _sse_line({"delta": payload})
        elif kind == "usage":
            yield _sse_line({"usage": payload})
        elif kind == "error":
            yield _sse_line({"error": payload})
            return
    yield _sse_line({"done": True})
