import asyncio
import json
import logging

from fastapi import HTTPException

import content_screen
import remote_rules

logger = logging.getLogger(__name__)

SSE_EVENT_END = b"\n\n"
LINE_END = b"\n"
DATA_PREFIX = b"data:"
TEXT_FIELDS = ("content", "reasoning_content", "reasoning", "refusal")
LEGACY_CALL_INDEX = -1
DONE_MARK = b"[DONE]"
MINORS_KIND = "minors"


def request_of(messages: list[dict]) -> str:
    return remote_rules.latest_request(messages)


def context_before(messages: list[dict]) -> str:
    first = remote_rules.first_unanswered(messages)
    return remote_rules.conversation_text([message for index, message in enumerate(messages)
                                           if index < first or message.get("role") in content_screen.SYSTEM_ROLES])


def reply_context(messages: list[dict]) -> str:
    return remote_rules.conversation_text(messages)


def refused_without_arty(said: str, context: str, refuses_when_unsure: bool = False) -> str | None:
    if refuses_when_unsure or content_screen.unsafe_without_a_judge(said, context):
        return content_screen.CANT_CHECK_REPLY
    return None


async def closer_look(model: str, messages: list[dict], said: str, context: str,
                      refuses_when_unsure: bool = False) -> str | None:
    try:
        rules = await remote_rules.rules_for(model, messages, said=said, may_be_unrestricted=True)
    except (HTTPException, ValueError, OSError) as error:
        logger.warning("minors closer look for %s: Arty did not answer (%s), only the plain under-18 signal is used",
                       model, type(error).__name__)
        return await asyncio.to_thread(refused_without_arty, said, context, refuses_when_unsure)
    return rules.declined if rules.declined_kind == MINORS_KIND else None


def local_verdict(said: str, context: str, wants_a_closer_look: bool, refuses_when_certain: bool = False) -> tuple[bool, bool]:
    if content_screen.sexual_with_minors(said, context):
        return True, False
    if refuses_when_certain and content_screen.refused_without_a_judge(said, context):
        return True, False
    if refuses_when_certain and content_screen.needs_the_judge_to_clear(said, context):
        return False, True
    return False, wants_a_closer_look and content_screen.needs_a_closer_look(said, context)


async def declined_request(model: str, messages: list[dict], arty_checks_it: bool = False) -> str | None:
    said = request_of(messages)
    if not said.strip():
        return None
    context = context_before(messages)
    unrestricted = remote_rules.is_unrestricted(model)
    wants_a_closer_look = not arty_checks_it and unrestricted
    refuses_when_certain = not arty_checks_it and not unrestricted
    refused, needs_arty = await asyncio.to_thread(local_verdict, said, context, wants_a_closer_look, refuses_when_certain)
    if refused:
        return content_screen.MINORS_REPLY
    if not needs_arty:
        return None
    return await closer_look(model, messages, said, context, refuses_when_certain)


def checked_text(text: str, messages: list[dict]) -> str:
    return content_screen.declined_reply(text, reply_context(messages), request_of(messages)) or text


async def checked_collected(model: str, messages: list[dict], reply) -> str:
    try:
        declined = await declined_request(model, messages)
    except BaseException:
        reply.close()
        raise
    if declined:
        reply.close()
        return declined
    return await asyncio.to_thread(checked_text, await reply, messages)


def openai_refusal_chunk(model: str) -> bytes:
    chunk = {"id": "chatcmpl-minors", "object": "chat.completion.chunk", "model": model,
             "choices": [{"index": 0, "delta": {"content": content_screen.REFUSAL_DELTA}, "finish_reason": "stop"}]}
    return b"data: " + json.dumps(chunk).encode() + SSE_EVENT_END + b"data: " + DONE_MARK + SSE_EVENT_END


def event_payload(event: bytes):
    lines = [line[len(DATA_PREFIX):].strip() for line in event.split(b"\n") if line.startswith(DATA_PREFIX)]
    if not lines:
        return None
    data = b"".join(lines)
    if data == DONE_MARK:
        return DONE_MARK
    try:
        return json.loads(data)
    except ValueError:
        return None


def texts_in(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        return []
    return [part if isinstance(part, str) else part.get("text") for part in value
            if isinstance(part, str) or (isinstance(part, dict) and isinstance(part.get("text"), str))]


def message_texts(message: dict) -> list[str]:
    return [text for field in TEXT_FIELDS for text in texts_in(message.get(field))]


def message_calls(message: dict) -> list:
    calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
    legacy = message.get("function_call")
    return calls + ([{"function": legacy}] if isinstance(legacy, dict) else [])


def line_payload(line: bytes):
    return event_payload(line) if line.startswith(DATA_PREFIX) else None


class OpenAIStreamGuard:
    def __init__(self, context: str, request: str):
        self.guard = content_screen.ReplyGuard(context, request)
        self.arguments: dict[int, str] = {}
        self.held: list[bytes] = []

    def read(self, payload) -> tuple[bool, bool]:
        if not isinstance(payload, dict):
            return False, False
        holds, finished = False, False
        for choice in payload.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            if any(self.guard.add(text) for text in message_texts(delta)):
                return True, False
            for call in delta.get("tool_calls") if isinstance(delta.get("tool_calls"), list) else []:
                holds = True
                function = call.get("function") if isinstance(call, dict) and isinstance(call.get("function"), dict) else {}
                index = call.get("index") if isinstance(call, dict) and isinstance(call.get("index"), int) else 0
                self.add_arguments(index, function)
            if isinstance(delta.get("function_call"), dict):
                holds = True
                self.add_arguments(LEGACY_CALL_INDEX, delta["function_call"])
            finished = finished or bool(choice.get("finish_reason"))
        return False, holds or (finished and bool(self.held))

    def add_arguments(self, index: int, function: dict):
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            self.arguments[index] = self.arguments.get(index, "") + arguments
        elif arguments is not None:
            self.arguments[index] = self.arguments.get(index, "") + "\n" + content_screen.call_arguments({"function": function})

    def calls_cross_the_line(self) -> bool:
        calls = [{"function": {"arguments": text}} for text in self.arguments.values()]
        return self.guard.add("\n" + "\n".join(content_screen.call_arguments(call) for call in calls)) or self.guard.finish()


def is_ending(payload) -> bool:
    return payload == DONE_MARK or (isinstance(payload, dict) and any(
        isinstance(choice, dict) and choice.get("finish_reason") for choice in payload.get("choices") or []))


def next_unit(pending: list[bytes], line: bytes):
    pending.append(line + LINE_END)
    payload = line_payload(line)
    if payload is not None:
        return b"".join(pending), payload
    if line.strip():
        return None
    return b"".join(pending), event_payload(b"".join(pending))


def passed_on(watch: OpenAIStreamGuard, data: bytes, payload) -> tuple[bool, list[bytes]]:
    if payload is None:
        if watch.held:
            watch.held.append(data)
            return False, []
        return False, [data]
    ending = is_ending(payload)
    crossed, holds = watch.read(payload)
    if ending and not crossed:
        crossed = watch.calls_cross_the_line()
    if crossed:
        return True, []
    if holds and not ending:
        watch.held.append(data)
        return False, []
    released, watch.held = watch.held + [data], []
    return False, released


async def guarded_openai_stream(chunks, context: str, request: str, model: str):
    watch = OpenAIStreamGuard(context, request)
    waiting = b""
    pending: list[bytes] = []
    try:
        async for chunk in chunks:
            waiting += chunk.replace(b"\r\n", b"\n")
            while LINE_END in waiting:
                line, waiting = waiting.split(LINE_END, 1)
                unit = next_unit(pending, line)
                if unit is None:
                    continue
                pending = []
                crossed, released = passed_on(watch, *unit)
                if crossed:
                    yield openai_refusal_chunk(model)
                    return
                for data in released:
                    yield data
        leftover = b"".join(pending) + waiting
        if leftover:
            crossed, released = passed_on(watch, leftover, event_payload(leftover))
            if crossed:
                yield openai_refusal_chunk(model)
                return
            for data in released:
                yield data
        if watch.held:
            if watch.calls_cross_the_line():
                yield openai_refusal_chunk(model)
                return
            for held in watch.held:
                yield held
    finally:
        await chunks.aclose()


def guarded_openai_whole(content: bytes, context: str, request: str) -> bytes:
    try:
        reply = json.loads(content)
    except ValueError:
        text = content.decode("utf-8", errors="replace")
        return json.dumps(openai_refused_whole()).encode() if content_screen.reply_crosses_the_line(text, context, request) \
            else content
    if not isinstance(reply, dict):
        text = content.decode("utf-8", errors="replace")
        return json.dumps(openai_refused_whole()).encode() if content_screen.reply_crosses_the_line(text, context, request) \
            else content
    crossed = False
    for choice in reply.get("choices") or []:
        message = choice.get("message") if isinstance(choice, dict) and isinstance(choice.get("message"), dict) else {}
        text = "\n".join(message_texts(message))
        if content_screen.calls_cross_the_line(text, message_calls(message), context, request):
            choice["message"] = {"role": "assistant", "content": content_screen.MINORS_REPLY}
            choice["finish_reason"] = "stop"
            crossed = True
    return json.dumps(reply).encode() if crossed else content


def openai_refused_whole() -> dict:
    return {"id": "chatcmpl-minors", "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content_screen.MINORS_REPLY},
                         "finish_reason": "stop"}]}
