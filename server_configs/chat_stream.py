import json
import logging
import math

import httpx

from ollama_runner import for_runner
from providers import _detect_provider, build_chat_url, build_headers, build_tools_param

MAX_ERROR_CHARS = 500
NO_ERROR_DETAILS = "the model server gave no details"
NANOSECONDS_PER_SECOND = 1_000_000_000
MILLISECONDS_PER_SECOND = 1000
STREAM_OPTIONS_FIELD = "stream_options"
USAGE_IN_STREAM = {"include_usage": True}
REPLY_TOO_LONG = "too_long"
IDENTITY_ENCODING = "identity"
CONNECT_PHASE_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)

class BrokenOffAfterSending(str):
    pass

class NeverReachedServer(str):
    pass

ROLE_FIELD = "role"
OUTPUT_EVENT_TYPES = {"content_block_start", "content_block_delta"}

def _carries_output(piece: dict) -> bool:
    return any(value for name, value in piece.items() if name != ROLE_FIELD)

def _failure(message: str, heard_output: bool) -> str:
    return BrokenOffAfterSending(message) if heard_output else message

logger = logging.getLogger(__name__)
TEXT_PART = "text"
MAX_INDEX_DIGITS = 9
MAX_TOKEN_COUNT = 10**12

def _is_text_part(part) -> bool:
    return isinstance(part, dict) and part.get("type") == TEXT_PART and isinstance(part.get("text"), str)

def _is_whole_number(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, float):
        return value.is_integer()
    if isinstance(value, str):
        return value.isascii() and value.isdigit() and len(value) <= MAX_INDEX_DIGITS
    return isinstance(value, int)

class _ReplyShapes:

    def __init__(self, reader: str):
        self.reader = reader
        self.reported = False

    def skip(self, value, field: str) -> None:
        if value is None or self.reported:
            return
        self.reported = True
        logger.warning("%s reply: skipped the %s because it was sent as %s; later skips in this reply are not logged",
                       self.reader, field, type(value).__name__)

    def drop_nameless_call(self) -> None:
        if self.reported:
            return
        self.reported = True
        logger.warning("%s reply: dropped a tool call with no name; later skips in this reply are not logged",
                       self.reader)

    def object(self, value, field: str) -> dict:
        if isinstance(value, dict):
            return value
        self.skip(value, field)
        return {}

    def items(self, value, field: str) -> list:
        if isinstance(value, list):
            return value
        self.skip(value, field)
        return []

    def objects(self, value, field: str) -> list[dict]:
        found = []
        for each in self.items(value, f"{field} list"):
            if isinstance(each, dict):
                found.append(each)
            else:
                self.skip(each, field)
        return found

    def text(self, value, field: str) -> str:
        if isinstance(value, str):
            return value
        self.skip(value, field)
        return ""

    def content(self, value, field: str) -> str:
        if isinstance(value, list):
            return "".join(part["text"] for part in value if _is_text_part(part))
        return self.text(value, field)

    def identifier(self, value, field: str) -> str:
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return self.text(value, field)

    def position(self, value, field: str) -> int:
        if _is_whole_number(value):
            return int(value)
        self.skip(value, field)
        return 0

    def arguments(self, value, field: str) -> str:
        if isinstance(value, dict):
            return _serialised_arguments(value)
        return self.text(value, field)

UNREADABLE_JSON = (ValueError, RecursionError)

def _refuse_constant(name: str):
    raise ValueError(f"{name} is not a finite number")

def _finite_float(text: str) -> float:
    number = float(text)
    if not math.isfinite(number):
        raise ValueError(f"{text} is not a finite number")
    return number

def _parsed_arguments(text: str) -> dict:
    if not text:
        return {}
    try:
        parsed = json.loads(text, parse_constant=_refuse_constant, parse_float=_finite_float)
    except UNREADABLE_JSON:
        return {}
    return parsed if isinstance(parsed, dict) else {}

def _serialised_arguments(value: dict) -> str:
    if not value:
        return ""
    try:
        return json.dumps(value, allow_nan=False)
    except UNREADABLE_JSON:
        return ""

def _finite_arguments(value):
    if isinstance(value, dict) and value and not _serialised_arguments(value):
        return {}
    return value

def _history_arguments(value) -> dict:
    if isinstance(value, str):
        return _parsed_arguments(value)
    return _finite_arguments(value) if isinstance(value, dict) else {}

LINE_BREAK = "\n"

async def _capped_lines(r: httpx.Response, max_bytes: int):
    unfinished: list[str] = []
    received = 0
    async for text in r.aiter_text():
        received += len(text)
        if received > max_bytes:
            yield None
            return
        *finished, rest = text.split(LINE_BREAK)
        if finished:
            finished[0] = "".join(unfinished) + finished[0]
            unfinished = []
        for line in finished:
            yield line
        if rest:
            unfinished.append(rest)
    if unfinished:
        yield "".join(unfinished)

def _lines(r: httpx.Response, max_bytes: int | None):
    return r.aiter_lines() if max_bytes is None else _capped_lines(r, max_bytes)

def _sse_line(payload: dict) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()

def _is_finite_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return not isinstance(value, float) or math.isfinite(value)

def _token_count(value) -> int | None:
    if not _is_finite_number(value) or not 0 <= value <= MAX_TOKEN_COUNT:
        return None
    return int(value)

def _seconds(value, units_per_second: int) -> float | None:
    if not _is_finite_number(value) or value <= 0:
        return None
    try:
        return value / units_per_second
    except OverflowError:
        return None

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
    timed_prompt_tokens = _token_count(timings.get("prompt_n"))
    if timed_prompt_tokens is not None:
        cached_prompt_tokens = _token_count(timings.get("cache_n"))
        prompt_tokens = timed_prompt_tokens + (cached_prompt_tokens or 0)
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
                content.append({"type": "tool_use", "id": tc.get("id"), "name": fn.get("name"),
                                "input": _history_arguments(fn.get("arguments"))})
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
                calls.append({"function": {"name": fn.get("name"),
                                           "arguments": _history_arguments(fn.get("arguments"))}})
            out.append({"role": "assistant", "content": m.get("content", ""), "tool_calls": calls})
        else:
            out.append(m)
    return out

async def _iter_ollama(client: httpx.AsyncClient, url: str, headers: dict, payload: dict,
                       max_bytes: int | None = None):
    async with client.stream("POST", url, headers=headers, json=payload) as r:
        if r.is_error:
            body = await r.aread()
            yield ("error", f"Ollama {r.status_code}: {_refusal_text(body)}")
            return
        heard_output = False
        shapes = _ReplyShapes("NDJSON")
        async for line in _lines(r, max_bytes):
            if line is None:
                yield (REPLY_TOO_LONG, max_bytes)
                return
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except UNREADABLE_JSON:
                continue
            if not isinstance(obj, dict):
                shapes.skip(obj, "line")
                continue
            if obj.get("error"):
                yield ("error", _failure(f"Ollama: {obj['error']}", heard_output))
                return
            message = shapes.object(obj.get("message"), "message")
            heard_output = heard_output or _carries_output(message)
            content = shapes.content(message.get("content"), "content")
            if content:
                yield ("delta", content)
            raw_calls = shapes.objects(message.get("tool_calls"), "tool call")
            if raw_calls:
                calls = []
                for i, c in enumerate(raw_calls):
                    fn = shapes.object(c.get("function"), "function")
                    name = shapes.text(fn.get("name"), "tool name")
                    if not name:
                        shapes.drop_nameless_call()
                        continue
                    args = fn.get("arguments")
                    args = _parsed_arguments(args) if isinstance(args, str) else _finite_arguments(args)
                    calls.append({"id": shapes.identifier(c.get("id"), "tool call id") or f"ollama-{i}",
                                  "name": name, "arguments": shapes.object(args, "arguments")})
                if calls:
                    yield ("tool_calls", calls)
            if obj.get("done"):
                usage = _ollama_usage(obj, payload)
                if usage:
                    yield ("usage", usage)
                return

async def _iter_anthropic(client: httpx.AsyncClient, url: str, headers: dict, payload: dict,
                          max_bytes: int | None = None):
    async with client.stream("POST", url, headers=headers, json=payload) as r:
        if r.is_error:
            body = await r.aread()
            yield ("error", f"Anthropic {r.status_code}: {_refusal_text(body)}")
            return

        event_type = None
        tool_blocks: dict[int, dict] = {}
        prompt_usage: dict = {}
        output_tokens = None
        heard_output = False
        shapes = _ReplyShapes("typed-event")

        async for raw in _lines(r, max_bytes):
            if raw is None:
                yield (REPLY_TOO_LONG, max_bytes)
                return
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
            except UNREADABLE_JSON:
                continue
            if not isinstance(obj, dict):
                shapes.skip(obj, "event")
                continue

            heard_output = heard_output or event_type in OUTPUT_EVENT_TYPES
            if event_type == "message_start":
                prompt_usage = shapes.object(shapes.object(obj.get("message"), "message").get("usage"), "usage")
                output_tokens = prompt_usage.get("output_tokens", output_tokens)
            elif event_type == "content_block_start":
                block = shapes.object(obj.get("content_block"), "content block")
                if block.get("type") == "tool_use":
                    tool_blocks[shapes.position(obj.get("index"), "index")] = {
                        "id": shapes.identifier(block.get("id"), "tool call id") or None,
                        "name": shapes.text(block.get("name"), "tool name") or None, "json_buf": "",
                    }
            elif event_type == "content_block_delta":
                delta = shapes.object(obj.get("delta"), "delta")
                idx = shapes.position(obj.get("index"), "index")
                text = shapes.text(delta.get("text"), "text")
                if delta.get("type") == "text_delta" and text:
                    yield ("delta", text)
                elif delta.get("type") == "input_json_delta" and idx in tool_blocks:
                    tool_blocks[idx]["json_buf"] += shapes.text(delta.get("partial_json"), "arguments")
            elif event_type == "error":
                reported = obj.get("error")
                found = reported if isinstance(reported, str) else shapes.object(reported, "error").get("message")
                message = _error_message(found or "Anthropic stream error")
                yield ("error", _failure(message, heard_output))
                return
            elif event_type == "message_delta":
                output_tokens = shapes.object(obj.get("usage"), "usage").get("output_tokens", output_tokens)
                if shapes.object(obj.get("delta"), "delta").get("stop_reason") == "tool_use" and tool_blocks:
                    calls = []
                    for block in tool_blocks.values():
                        if not block["name"]:
                            shapes.drop_nameless_call()
                            continue
                        calls.append({"id": block["id"], "name": block["name"],
                                      "arguments": _parsed_arguments(block["json_buf"])})
                    if calls:
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
    except UNREADABLE_JSON:
        parsed = None
    if isinstance(parsed, dict) and parsed.get("error"):
        text = _error_message(parsed["error"])
    return text[:MAX_ERROR_CHARS] or NO_ERROR_DETAILS

async def _iter_openai(client: httpx.AsyncClient, url: str, headers: dict, payload: dict,
                       max_bytes: int | None = None):
    async with client.stream("POST", url, headers=headers, json=payload) as r:
        if r.is_error:
            body = await r.aread()
            yield ("error", f"{r.status_code}: {_refusal_text(body)}")
            return

        call_acc: dict[int, dict] = {}
        finish_reason = None
        reported_usage = None
        reported_timings = None
        heard_output = False
        shapes = _ReplyShapes("OpenAI-style")

        async for raw in _lines(r, max_bytes):
            if raw is None:
                yield (REPLY_TOO_LONG, max_bytes)
                return
            line = raw.strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except UNREADABLE_JSON:
                continue
            if not isinstance(obj, dict):
                shapes.skip(obj, "line")
                continue
            if obj.get("error"):
                yield ("error", _failure(_error_message(obj["error"]), heard_output))
                return
            reported_usage = obj.get("usage") or reported_usage
            reported_timings = obj.get("timings") or reported_timings
            choices = shapes.items(obj.get("choices"), "choice list")
            if not choices:
                continue
            choice = choices[0]
            if not isinstance(choice, dict):
                shapes.skip(choice, "choice")
                continue
            delta = shapes.object(choice.get("delta"), "delta")
            heard_output = heard_output or _carries_output(delta)
            content = shapes.content(delta.get("content"), "content")
            if content:
                yield ("delta", content)
            for tc in shapes.objects(delta.get("tool_calls"), "tool call"):
                slot = call_acc.setdefault(shapes.position(tc.get("index"), "index"),
                                           {"id": None, "name": None, "args_buf": ""})
                call_id = shapes.identifier(tc.get("id"), "tool call id")
                if call_id:
                    slot["id"] = call_id
                fn = shapes.object(tc.get("function"), "function")
                name = shapes.text(fn.get("name"), "tool name")
                if name:
                    slot["name"] = name
                slot["args_buf"] += shapes.arguments(fn.get("arguments"), "arguments")
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]

        if finish_reason == "tool_calls" and call_acc:
            calls = []
            for i, slot in sorted(call_acc.items()):
                if not slot["name"]:
                    shapes.drop_nameless_call()
                    continue
                calls.append({"id": slot["id"] or f"call-{i}", "name": slot["name"],
                              "arguments": _parsed_arguments(slot["args_buf"])})
            if calls:
                yield ("tool_calls", calls)

        usage = _openai_usage(reported_usage, reported_timings)
        if usage:
            yield ("usage", usage)

async def _iter_openai_asking_for_usage(client: httpx.AsyncClient, url: str, headers: dict, payload: dict,
                                        max_bytes: int | None = None):
    refused_the_usage_option = False
    said_anything = False
    async for kind, item in _iter_openai(client, url, headers, {**payload, STREAM_OPTIONS_FIELD: USAGE_IN_STREAM},
                                         max_bytes):
        if kind == "error" and not said_anything and STREAM_OPTIONS_FIELD in item:
            refused_the_usage_option = True
            continue
        said_anything = True
        yield kind, item
    if refused_the_usage_option:
        async for each in _iter_openai(client, url, headers, payload, max_bytes):
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
                            think: bool | None = None, max_bytes: int | None = None):
    provider = _detect_provider(base_url)
    url = build_chat_url(base_url)
    headers = build_headers(api_key, base_url)
    if max_bytes is not None:
        headers = {**headers, "Accept-Encoding": IDENTITY_ENCODING}
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
                async for item in _iter_ollama(client, url, headers, payload, max_bytes):
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
                async for item in _iter_anthropic(client, url, headers, payload, max_bytes):
                    yield item
            else:
                payload = {
                    "model": model, "stream": True,
                    "messages": _build_messages(messages, system),
                    **_hosted_params("openai", options),
                    **({"tools": tools_param} if tools_param else {}),
                }
                async for item in _iter_openai_asking_for_usage(client, url, headers, payload, max_bytes):
                    yield item
        except CONNECT_PHASE_ERRORS as e:
            yield ("error", NeverReachedServer(e))
        except httpx.HTTPError as e:
            yield ("error", BrokenOffAfterSending(e))

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
