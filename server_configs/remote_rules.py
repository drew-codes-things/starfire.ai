import asyncio
import functools
import ipaddress
import json
import logging
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import HTTPException

import arty_devices
import content_screen
import private_text
from config import config
from model_access import matches_any

logger = logging.getLogger(__name__)

RULES_PATH = "/v1/rules"
SSE_PREFIX = "data:"
TEXT_ENDS = {"done", "error", "usage", "reply", "tool_calls", "finish_reason"}
SSE_EVENT_END = b"\n\n"
BRAIN_TOO_OLD = 404
ARTY_OUT_OF_DATE = "Arty is out of date: update Arty so starfire can read its rules for this model."
REMOTE_MODEL_PREFIX = "remote/"
SYSTEM_ROLES = frozenset({"system", "developer"})
CALLER_ROLES = frozenset({"user", "tool"})
ASSISTANT_ROLE = "assistant"
RULED_ROLES = SYSTEM_ROLES | CALLER_ROLES | {ASSISTANT_ROLE}
TEXT_PART = "text"
MAX_ROLE_SHOWN = 40
TEXT_PART_SHAPE = '{"type": "text", "text": "..."}'
RESOLVED_SECONDS = 300
resolved_cache: dict[str, tuple[float, frozenset[str]]] = {}
TOOL_ROLE = "tool"
TOOL_CALL_ID = "tool_call_id"
FUNCTION_TYPE = "function"
RULES_TEXT_LABEL = "Arty's rules"
ARTY_CHECK_SECONDS = 6
ARTY_CLOSER_LOOK_QUEUE_SECONDS = 2
ARTY_CLOSER_LOOK_SECONDS = 45
RULES_MARGIN_SECONDS = 9
RULES_SECONDS = float(ARTY_CHECK_SECONDS + ARTY_CLOSER_LOOK_QUEUE_SECONDS + ARTY_CLOSER_LOOK_SECONDS + RULES_MARGIN_SECONDS)
USER_ROLE = "user"
LEFT_OUT_NOTE = ("Some earlier messages in this conversation were left out because they can't be shown under these rules; "
                 "don't guess what they said.")
UNCHECKED_NOTE = ("Some earlier messages in this conversation were left out because there wasn't time to check them; "
                  "if you need them, ask the user to repeat what matters.")
OWN_DATA_REFUSAL = ("Remote models and connections that follow Arty's rules can't read your email: pick a local model "
                    "for this.")


@dataclass(frozen=True)
class ArtyRules:
    system: str
    declined: str | None
    declined_kind: str | None = None
    left_out: frozenset[int] = frozenset()
    unchecked: frozenset[int] = frozenset()


@dataclass(frozen=True)
class SaidParts:
    caller: str = ""
    standing: str = ""
    tools: str = ""
    offered_tools: str = ""


@dataclass(frozen=True)
class CallerText:
    said: str = ""
    answered: tuple[tuple[str, str], ...] = ()
    exchanges: tuple[tuple[int, ...], ...] = ()
    parts: SaidParts | None = None

    def everything(self) -> str:
        return joined_texts([self.said, *(text for exchange in self.answered for text in exchange)])


def host_of(endpoint) -> str:
    try:
        host = urlsplit(endpoint.base_url.strip()).hostname or ""
    except ValueError:
        return ""
    return host.rstrip(".").lower()


def plain_address(text: str) -> str:
    address = ipaddress.ip_address(text.split("%", 1)[0])
    mapped = getattr(address, "ipv4_mapped", None)
    return str(mapped or address)


def ip_of(host: str) -> str | None:
    try:
        return plain_address(host)
    except ValueError:
        return None


def resolved_addresses(host: str) -> frozenset[str]:
    now = time.monotonic()
    cached = resolved_cache.get(host)
    if cached and now - cached[0] < RESOLVED_SECONDS:
        return cached[1]
    try:
        found = frozenset(plain_address(info[4][0]) for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM))
    except (OSError, ValueError, UnicodeError):
        found = frozenset()
    resolved_cache[host] = (now, found)
    return found


def same_server(host: str, ruled: str) -> bool:
    if not host or not ruled:
        return False
    if host == ruled:
        return True
    host_addresses, ruled_addresses = addresses_of(host), addresses_of(ruled)
    if not host_addresses or not ruled_addresses:
        return False
    shares_a_home_address = any(is_home_address(address) for address in host_addresses & ruled_addresses)
    return shares_a_home_address or host_addresses <= ruled_addresses


def addresses_of(host: str) -> frozenset[str]:
    address = ip_of(host)
    return frozenset({address}) if address else resolved_addresses(host)


HOME_HOST_NAMES = frozenset({"localhost"})
HOME_NAME_SUFFIXES = (".local", ".lan", ".home.arpa")


def short_address(host: str) -> str | None:
    try:
        return str(ipaddress.IPv4Address(socket.inet_aton(host)))
    except (OSError, ValueError):
        return None


def is_home_name(host: str) -> bool:
    return "." not in host or host.endswith(HOME_NAME_SUFFIXES)


def is_home_address(text: str) -> bool:
    address = ipaddress.ip_address(text)
    return address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified


def is_home_host(host: str) -> bool:
    address = ip_of(host) or short_address(host)
    if address is not None:
        return is_home_address(address)
    return host in HOME_HOST_NAMES or is_home_name(host)


def is_remote_connection(endpoint) -> bool:
    if endpoint.id in config.remote_rules_connection_ids():
        return True
    host = host_of(endpoint)
    return bool(host) and not is_home_host(host)


def applies_to(endpoint, list_endpoints) -> bool:
    wanted = set(config.remote_rules_connection_ids())
    if not wanted or endpoint.id in wanted:
        return bool(wanted)
    host = host_of(endpoint)
    return any(same_server(host, host_of(known)) for known in list_endpoints() if known.id in wanted)


def missing_connections(known_endpoints) -> list[str]:
    known = {endpoint.id for endpoint in known_endpoints}
    return [connection for connection in config.remote_rules_connection_ids() if connection not in known]


def warn_about_missing_connections(known_endpoints) -> None:
    for connection in missing_connections(known_endpoints):
        logger.warning("REMOTE_RULES_CONNECTIONS names connection %s, which starfire doesn't have", connection)


def is_remote_model(model: str) -> bool:
    return (model or "").startswith(REMOTE_MODEL_PREFIX)


def reads_own_data_safely(endpoint, model: str, list_endpoints) -> bool:
    return not (applies_to(endpoint, list_endpoints) or is_remote_model(model))


def is_unrestricted(model: str) -> bool:
    return matches_any(model, config.restricted_model_patterns())


def allowed_tools(requested: set[str]) -> set[str]:
    return requested & set(config.remote_tool_names())


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    parts = content if isinstance(content, list) else []
    return "\n".join(part["text"] for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str))


def message_problem(messages: list[dict]) -> str | None:
    for index, message in enumerate(messages):
        role = message.get("role")
        if role not in RULED_ROLES:
            return f"message {index}: role {str(role)[:MAX_ROLE_SHOWN]!r} is not one of {', '.join(sorted(RULED_ROLES))}"
        content = message.get("content")
        if isinstance(content, list) and not all(is_text_part(part) for part in content):
            return f"message {index}: on this connection every content part must be {TEXT_PART_SHAPE}"
    return None


def is_text_part(part) -> bool:
    return isinstance(part, dict) and part.get("type") == TEXT_PART and isinstance(part.get("text"), str)


def texts_in(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for key, item in value.items() for text in (str(key), *texts_in(item))]
    if isinstance(value, list):
        return [text for item in value for text in texts_in(item)]
    return []


def parsed_arguments(arguments):
    if not isinstance(arguments, str):
        return arguments if arguments is not None else {}
    try:
        return json.loads(arguments)
    except (ValueError, RecursionError):
        return arguments


def canonical_arguments(arguments) -> str:
    parsed = parsed_arguments(arguments)
    return parsed if isinstance(parsed, str) else json.dumps(parsed, ensure_ascii=False)


def ruled_tool_call(call) -> dict:
    call = call if isinstance(call, dict) else {}
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    return {"id": str(call.get("id") or ""), "type": FUNCTION_TYPE,
            "function": {"name": str(function.get("name") or ""),
                         "arguments": canonical_arguments(function.get("arguments"))}}


def ruled_message(message: dict, kept_fields: tuple[str, ...] = ()) -> dict:
    role = message.get("role")
    ruled = {"role": role, "content": text_of(message.get("content"))}
    if role == TOOL_ROLE:
        ruled[TOOL_CALL_ID] = str(message.get(TOOL_CALL_ID) or "")
    calls = message.get("tool_calls")
    if role == ASSISTANT_ROLE and isinstance(calls, list) and calls:
        ruled["tool_calls"] = [ruled_tool_call(call) for call in calls]
    ruled.update({name: message[name] for name in kept_fields if name in message})
    return ruled


def ruled_messages(messages: list[dict], kept_fields: tuple[str, ...] = ()) -> list[dict]:
    return [ruled_message(message, kept_fields) for message in messages]


def ruled_tool(tool: dict) -> dict:
    function = tool.get("function") if isinstance(tool.get("function"), dict) else {}
    kept = {"name": function.get("name")}
    if isinstance(function.get("description"), str):
        kept["description"] = function["description"]
    if isinstance(function.get("parameters"), dict):
        kept["parameters"] = function["parameters"]
    return {"type": FUNCTION_TYPE, "function": kept}


def tool_texts(tools: list[dict]) -> list[str]:
    return [text for tool in tools for text in texts_in(tool.get("function"))]


def call_texts(call: dict) -> list[str]:
    function = call["function"]
    return [call["id"], function["name"], *texts_in(parsed_arguments(function["arguments"]))]


def message_texts(message: dict) -> list[str]:
    return [message["content"], str(message.get(TOOL_CALL_ID) or ""),
            *(text for call in message.get("tool_calls") or [] for text in call_texts(call))]


def written_by_nobody_else(message: dict) -> bool:
    return False


def ends_an_exchange(message: dict) -> bool:
    return message["role"] == ASSISTANT_ROLE and not message.get("tool_calls")


def joined_texts(texts) -> str:
    return "\n".join(text for text in texts if text and text.strip())


def asks_something(message: dict) -> bool:
    return message["role"] == USER_ROLE and bool(message["content"].strip())


def unanswered_rounds(ruled: list[dict], tail: list[int]) -> list[list[int]]:
    rounds = []
    for index in tail:
        if not rounds or (asks_something(ruled[index]) and any(asks_something(ruled[earlier]) for earlier in rounds[-1])):
            rounds.append([])
        rounds[-1].append(index)
    return rounds


def caller_text(messages: list[dict], also: tuple[str, ...] | list[str] = (),
                starfire_wrote=written_by_nobody_else, tools: tuple[str, ...] | list[str] = ()) -> CallerText:
    ruled = ruled_messages(messages)

    def texts(index: int) -> list[str]:
        return [] if starfire_wrote(messages[index]) else message_texts(ruled[index])

    def texts_of(indexes) -> str:
        return joined_texts(text for index in indexes for text in texts(index))

    turns = [index for index, message in enumerate(ruled) if message["role"] not in SYSTEM_ROLES]
    standing = [index for index, message in enumerate(ruled) if message["role"] in SYSTEM_ROLES]
    ends = [index for index in turns[:-1] if ends_an_exchange(ruled[index])]
    groups, start = [], 0
    for end in ends:
        groups.append(tuple(index for index in turns if start <= index <= end))
        start = end + 1
    *closed, newest = unanswered_rounds(ruled, [index for index in turns if index >= start]) or [[]]
    said = joined_texts([*(text for index in [*reversed(newest), *reversed(standing)] for text in texts(index)),
                         *also, *tools])
    parts = SaidParts(caller=texts_of(index for index in newest if ruled[index]["role"] == USER_ROLE),
                      standing=joined_texts([texts_of(standing), *also]),
                      tools=texts_of(index for index in newest if ruled[index]["role"] != USER_ROLE),
                      offered_tools=joined_texts(tools))
    exchanges = (*(tuple(unanswered) for unanswered in reversed(closed)), *reversed(groups))
    answered = tuple((texts_of(unanswered), "") for unanswered in reversed(closed)) + tuple(
        (texts_of(group[:-1]), texts_of(group[-1:])) for group in reversed(groups))
    return CallerText(said=said, answered=answered, exchanges=exchanges, parts=parts)


def left_out_messages(answer: dict, caller: CallerText, field: str = "left_out") -> frozenset[int]:
    if not caller.answered or (field != "left_out" and field not in answer):
        return frozenset()
    left_out = answer.get(field)
    if not isinstance(left_out, list) or not all(type(index) is int and 0 <= index < len(caller.exchanges)
                                                 for index in left_out):
        raise HTTPException(502, ARTY_OUT_OF_DATE)
    return frozenset(message for index in left_out for message in caller.exchanges[index])


def kept_messages(messages: list[dict], rules: "ArtyRules | None") -> list[dict]:
    if rules is None or not rules.left_out:
        return messages
    return [message for index, message in enumerate(messages) if index not in rules.left_out]


def first_unanswered(messages: list[dict]) -> int:
    last_from_caller = next((index for index in range(len(messages) - 1, -1, -1)
                             if messages[index].get("role") in CALLER_ROLES), None)
    if last_from_caller is None:
        return len(messages)
    return next((index + 1 for index in range(last_from_caller - 1, -1, -1)
                 if messages[index].get("role") == ASSISTANT_ROLE), 0)


def latest_request(messages: list[dict]) -> str:
    unanswered = [text_of(message.get("content")) for message in messages[first_unanswered(messages):]
                  if message.get("role") not in SYSTEM_ROLES]
    return "\n".join(text for text in reversed(unanswered) if text.strip())


def conversation_text(messages: list[dict]) -> str:
    return content_screen.earlier_text(text_of(message.get("content")) for message in messages
                                    if message.get("role") in content_screen.GUARD_ROLES)


async def rules_for(model: str, messages: list[dict], said: str | None = None,
                    may_be_unrestricted: bool = False, caller: CallerText | None = None) -> ArtyRules:
    if caller is None:
        caller = caller_text(messages) if said is None else CallerText(said=said)
    asked = {"model": model, "unrestricted": is_unrestricted(model) and may_be_unrestricted, "said": caller.said,
             "context": conversation_text(messages[:-1])}
    if caller.answered:
        asked["answered"] = [{"asked": asked_text, "answer": answer} for asked_text, answer in caller.answered]
    if caller.parts is not None:
        asked["said_parts"] = {"caller": caller.parts.caller, "standing": caller.parts.standing,
                               "tools": caller.parts.tools}
        if caller.parts.offered_tools:
            asked["said_parts"]["offered_tools"] = caller.parts.offered_tools
    try:
        answer = await arty_devices.ask_brain("POST", RULES_PATH, asked, timeout=RULES_SECONDS,
                                              passed_on=frozenset({BRAIN_TOO_OLD}))
    except HTTPException as error:
        if error.status_code == BRAIN_TOO_OLD:
            raise HTTPException(502, ARTY_OUT_OF_DATE) from error
        raise
    system, declined = answer.get("system"), answer.get("declined")
    if not isinstance(system, str) or not system.strip():
        raise ValueError("Arty sent no rules for this model")
    kind = answer.get("declined_kind")
    return ArtyRules(system=system, declined=declined if isinstance(declined, str) and declined else None,
                     declined_kind=kind if isinstance(kind, str) else None, left_out=left_out_messages(answer, caller),
                     unchecked=left_out_messages(answer, caller, "unchecked"))


def system_with_rules(rules: ArtyRules, *notes: str | None) -> str:
    left_out_note = LEFT_OUT_NOTE if rules.left_out - rules.unchecked else None
    unchecked_note = UNCHECKED_NOTE if rules.unchecked else None
    return "\n\n".join([rules.system, *(note.strip() for note in (*notes, left_out_note, unchecked_note)
                                         if note and note.strip())])


def checked_reply(text: str, messages: list[dict]) -> str:
    return without_em_dashes(content_screen.declined_reply(text, conversation_text(messages), latest_request(messages)) or text)


LINE_LEAD = r"[ \t>]*"
FENCED_CODE = (r"^" + LINE_LEAD + r"(?P<fence>`{3,}|~{3,}).*?(?:\n" + LINE_LEAD
               + r"(?P=fence)[`~]*[ \t\r]*(?=\n|\Z)|\Z)")
INDENTED_CODE = r"(?:\A|(?<=\n\n)|(?<=\n\r\n))(?:(?: {4}|\t)[^\n]*(?:\n|\Z))+"
INLINE_CODE_MAX_CHARS = 400
INLINE_CODE = (r"(?<!`)(?P<ticks>`+)(?!`)(?:[^\n]|\n(?![ \t\r]*\n)){0,%d}?(?<!`)(?P=ticks)(?!`)"
               % INLINE_CODE_MAX_CHARS)
HTML_CODE = r"(?i:<pre\b[^>\n]*>.*?(?:</pre[ \t]*>|\Z)|<code\b[^>\n]*>.*?(?:</code[ \t]*>|\Z))"
DASHES = r"\u2014(?:[ \t]*\u2014)*"
DASH_RUNS = (r"(?P<range>(?<=\d)[ \t]*" + DASHES + r"[ \t]*(?=\d))"
             r"|(?P<line_start>^(?P<indent>[ \t]*)" + DASHES + r"[ \t]*)"
             r"|(?P<line_end>(?<![ \t])[ \t]*" + DASHES + r"[ \t]*(?=\r?$))"
             r"|(?<![ \t])[ \t]*" + DASHES + r"[ \t]*")
EM_DASH_RULES = re.compile(
    r"(?P<code>" + FENCED_CODE + r"|" + INDENTED_CODE + r"|" + INLINE_CODE + r"|" + HTML_CODE + r")|" + DASH_RUNS,
    re.M | re.S)
NUMBER_RANGE_DASH = "-"
EM_DASH_STAND_IN = ", "
JSON_WHITESPACE = " \t\r\n"
JSON_OPENERS = ("{", "[")
JSON_STRING_MAX_CHARS = 2000
JSON_STRING = re.compile(r'"((?:[^"\\]++|\\.)*+)"')


def em_dash_replacement(found: re.Match) -> str:
    kinds = found.groupdict()
    if kinds.get("code") is not None:
        return found.group()
    if kinds["range"] is not None:
        return NUMBER_RANGE_DASH
    if kinds["line_start"] is not None:
        return kinds["indent"]
    if kinds["line_end"] is not None:
        return ""
    return EM_DASH_STAND_IN


def is_json_reply(text: str) -> bool:
    if not text.lstrip(JSON_WHITESPACE).startswith(JSON_OPENERS):
        return False
    try:
        json.loads(text)
    except (ValueError, RecursionError):
        return False
    return not has_a_long_json_string(text)


def has_a_long_json_string(text: str) -> bool:
    return any(len(found.group(1)) > JSON_STRING_MAX_CHARS for found in JSON_STRING.finditer(text))


def without_em_dashes(text: str) -> str:
    if is_json_reply(text):
        return text
    return EM_DASH_RULES.sub(em_dash_replacement, text)


EM_DASH = "\u2014"
PROSE_DASH_RULES = re.compile(DASH_RUNS, re.M)
LINE_START = "x\n"
PLAIN_CONTEXT = "x"
DIGIT_CONTEXT = "0"
DIGIT = re.compile(r"\d")
HELD_AT_THE_END = " \t\r" + EM_DASH
LEAD_CHARACTERS = " \t>"
FENCE_MARKS = "`~"
CLOSING_FENCE_CHARACTERS = frozenset(" \t>`~\r")
LONGEST_CLOSING_FENCE = 200
SHORTEST_FENCE = 3
INDENT = "    "
TAB = "\t"
BLANK_LINES = ("", "\r")
PROSE_STOP = re.compile(r"[`<\n]")
TICK_RUN = re.compile(r"`+")
INLINE_STOP = re.compile(r"`+|\n[ \t\r]*")
HTML_OPENER = re.compile(r"<(pre|code)\b[^>\n]*>", re.I)
HTML_OPENER_IN_ATTRIBUTES = re.compile(r"<(?:pre|code)[^\w>\n]", re.I)
TAG_STOP = re.compile(r"[>\n]")
HTML_OPENER_SO_FAR = re.compile(r"<(?:p(?:r(?:e(?:\b[^>\n]*)?)?)?|c(?:o(?:d(?:e(?:\b[^>\n]*)?)?)?)?)?", re.I)
HTML_CLOSERS = {"pre": re.compile(r"</pre[ \t]*>", re.I), "code": re.compile(r"</code[ \t]*>", re.I)}
HTML_CLOSERS_SO_FAR = {"pre": re.compile(r"<(?:/(?:p(?:r(?:e[ \t]*)?)?)?)?", re.I),
                       "code": re.compile(r"<(?:/(?:c(?:o(?:d(?:e[ \t]*)?)?)?)?)?", re.I)}
START, JSON_REPLY, HEAD, PROSE, INLINE, TAG, HTML = "start", "json", "head", "prose", "inline", "tag", "html"
FENCE_OPENER, FENCE_INFO, FENCE_BODY, INDENTED = "fence_opener", "fence_info", "fence_body", "indented"
WAITING_MODES = frozenset({JSON_REPLY, INLINE})


def cleaned_prose(context: str, text: str, after: str = "") -> str:
    cleaned = PROSE_DASH_RULES.sub(em_dash_replacement, context + text + after)
    return cleaned[len(context):len(cleaned) - len(after)]


def context_after(text: str) -> str:
    if text.endswith("\n"):
        return LINE_START
    return DIGIT_CONTEXT if DIGIT.match(text[-1]) else PLAIN_CONTEXT


def lead_length(text: str) -> int:
    return len(text) - len(text.lstrip(LEAD_CHARACTERS))


def run_length(text: str, start: int) -> int:
    rest = text[start:]
    return len(rest) - len(rest.lstrip(rest[:1])) if rest else 0


def fence_closes(line: str, fence: str) -> bool:
    return re.fullmatch(LINE_LEAD + re.escape(fence) + r"[`~]*[ \t\r]*", line) is not None


def opens_a_fence(head: str) -> bool:
    lead = lead_length(head)
    return head[lead:lead + 1] in tuple(FENCE_MARKS) and run_length(head, lead) >= SHORTEST_FENCE


def may_still_open_a_fence(head: str) -> bool:
    marks = head[lead_length(head):]
    return len(marks) < SHORTEST_FENCE and marks in ("", marks[:1] * len(marks)) and set(marks) <= set(FENCE_MARKS)


def starts_indented(line: str) -> bool:
    return line.startswith((INDENT, TAB))


def may_still_be_indented(line: str) -> bool:
    return len(line) < len(INDENT) and line == " " * len(line)


JSON_LITERALS = ("true", "false", "null", "NaN", "Infinity", "-Infinity")
JSON_NUMBER_CHARACTERS = frozenset("+-0123456789.eE")
JSON_ESCAPES = frozenset('"\\/bfnrtu')
JSON_HEX = frozenset("0123456789abcdefABCDEF")
JSON_UNICODE_DIGITS = 4
FIRST_CONTROL_CHARACTER = 0x20
JSON_CLOSERS = {"{": "}", "[": "]"}
VALUE, FIRST_KEY, KEY, COLON, FIRST_VALUE, AFTER_VALUE, FINISHED = range(7)


class JsonPrefix:
    def __init__(self):
        self.open = []
        self.expect = VALUE
        self.in_string = False
        self.string_length = 0
        self.escape = None
        self.atom = None
        self.atom_is_number = False
        self.valid = True

    def feed(self, text: str) -> bool:
        for character in text:
            if not self.valid:
                break
            self.valid = self.take(character)
        return self.valid

    def take(self, character: str) -> bool:
        if self.in_string:
            return self.take_in_string(character)
        if self.atom is not None:
            if self.extends_atom(character):
                return True
            self.atom = None
            self.after_a_value()
        if character in JSON_WHITESPACE:
            return True
        return self.take_token(character)

    def take_in_string(self, character: str) -> bool:
        if self.escape is None and character == '"':
            return self.ends_string()
        self.string_length += 1
        if self.string_length > JSON_STRING_MAX_CHARS:
            return False
        if self.escape is not None:
            return self.take_escape(character)
        if character == "\\":
            self.escape = ""
            return True
        return ord(character) >= FIRST_CONTROL_CHARACTER

    def ends_string(self) -> bool:
        self.in_string = False
        if self.expect in (FIRST_KEY, KEY):
            self.expect = COLON
        else:
            self.after_a_value()
        return True

    def take_escape(self, character: str) -> bool:
        if self.escape == "":
            if character not in JSON_ESCAPES:
                return False
            self.escape = "u" if character == "u" else None
            return True
        if character not in JSON_HEX:
            return False
        self.escape += character
        if len(self.escape) > JSON_UNICODE_DIGITS:
            self.escape = None
        return True

    def extends_atom(self, character: str) -> bool:
        grown = self.atom + character
        if self.atom_is_number and character in JSON_NUMBER_CHARACTERS:
            self.atom = grown
            return True
        if any(literal.startswith(grown) for literal in JSON_LITERALS):
            self.atom, self.atom_is_number = grown, False
            return True
        return False

    def take_token(self, character: str) -> bool:
        if self.expect in (FIRST_KEY, KEY):
            return self.starts_key(character)
        if self.expect == COLON:
            if character != ":":
                return False
            self.expect = VALUE
            return True
        if self.expect == AFTER_VALUE:
            return self.takes_comma_or_close(character)
        if self.expect in (VALUE, FIRST_VALUE):
            return self.starts_value(character)
        return False

    def starts_key(self, character: str) -> bool:
        if character == "}" and self.expect == FIRST_KEY:
            return self.closes(character)
        if character != '"':
            return False
        self.start_string()
        return True

    def starts_value(self, character: str) -> bool:
        if character == "]" and self.expect == FIRST_VALUE:
            return self.closes(character)
        if character in JSON_CLOSERS:
            self.open.append(character)
            self.expect = FIRST_KEY if character == "{" else FIRST_VALUE
            return True
        if character == '"':
            self.start_string()
            return True
        if character in JSON_NUMBER_CHARACTERS or any(literal.startswith(character) for literal in JSON_LITERALS):
            self.atom, self.atom_is_number = character, character in JSON_NUMBER_CHARACTERS
            return True
        return False

    def start_string(self):
        self.in_string, self.string_length = True, 0

    def takes_comma_or_close(self, character: str) -> bool:
        if character == ",":
            self.expect = KEY if self.open[-1] == "{" else VALUE
            return True
        return self.closes(character)

    def closes(self, character: str) -> bool:
        if not self.open or JSON_CLOSERS[self.open[-1]] != character:
            return False
        self.open.pop()
        self.after_a_value()
        return True

    def after_a_value(self):
        self.expect = AFTER_VALUE if self.open else FINISHED


class DashlessText:
    def __init__(self, json_possible: bool = True):
        self.mode = START if json_possible else HEAD
        self.text = ""
        self.cursor = 0
        self.tail = 0
        self.context = LINE_START
        self.out = []
        self.final = False
        self.lines_ended = 0
        self.line_head = ""
        self.after_blank_line = True
        self.in_indented_block = False
        self.fence = ""
        self.closing_line = ""
        self.html_tag = ""
        self.waiting = []
        self.ticks = 0
        self.inline_carry = ""
        self.inline_base = 0
        self.inline_next = 0
        self.held_back = []
        self.json = JsonPrefix()
        self.json_read = 0

    def add(self, piece: str) -> str:
        if self.changes_nothing(piece):
            self.held_back.append(piece)
            return ""
        self.take_held_back()
        if self.mode in WAITING_MODES:
            self.waiting.append(piece)
        else:
            self.text += piece
        return self.run(final=False)

    def finish(self) -> str:
        self.take_held_back()
        return self.run(final=True)

    def changes_nothing(self, piece: str) -> bool:
        if self.mode == START:
            return not piece.strip(JSON_WHITESPACE)
        if self.mode == PROSE:
            return not piece.strip(HELD_AT_THE_END)
        if self.mode == HEAD:
            return not self.in_indented_block and not (self.text + piece).strip(LEAD_CHARACTERS)
        return self.mode == TAG and HTML_OPENER_IN_ATTRIBUTES.match(self.text) is not None and not TAG_STOP.search(piece)

    def take_held_back(self):
        if self.held_back:
            self.text, self.held_back = self.text + "".join(self.held_back), []

    def run(self, final: bool) -> str:
        self.final = final
        while getattr(self, "step_" + self.mode)():
            pass
        out, self.out = "".join(self.out), []
        return out

    def consumed(self, text: str):
        newlines = text.count("\n")
        if not newlines:
            self.line_head = (self.line_head + text)[:2]
            return
        parts = text.rsplit("\n", 2)
        ended = (self.line_head + parts[0])[:2] if newlines == 1 else parts[-2][:2]
        self.after_blank_line = ended in BLANK_LINES and self.lines_ended + newlines - 1 >= 1
        self.lines_ended += newlines
        self.line_head = parts[-1][:2]

    def send_raw(self, end: int):
        sent = self.text[:end]
        if sent:
            self.out.append(sent)
            self.consumed(sent)
            self.context = context_after(sent)
        self.text = self.text[end:]
        self.cursor = max(0, self.cursor - end)

    def send_prose(self, end: int, after: str = ""):
        sent = self.text[:end]
        if sent:
            self.out.append(cleaned_prose(self.context, sent, after))
            self.consumed(sent)
            self.context = context_after(sent)
        self.text = self.text[end:]
        self.cursor = max(0, self.cursor - end)
        self.tail = min(self.tail, self.cursor)

    def read_prose(self, end: int):
        read = self.text[self.cursor:end]
        kept = read.rstrip(HELD_AT_THE_END)
        self.tail = len(read) - len(kept) if kept else self.tail + len(read)
        self.cursor = end

    def to_prose(self, cursor: int = 0):
        self.mode, self.cursor, self.tail = PROSE, cursor, 0

    def step_start(self) -> bool:
        lead = self.text.lstrip(JSON_WHITESPACE)
        if not lead and not self.final:
            return False
        if lead.startswith(JSON_OPENERS):
            self.mode, self.waiting, self.text, self.json_read = JSON_REPLY, [self.text], "", 0
        else:
            self.mode = HEAD
        return True

    def step_json(self) -> bool:
        while self.json_read < len(self.waiting) and self.json.valid:
            self.json.feed(self.waiting[self.json_read])
            self.json_read += 1
        if self.json.valid and not self.final:
            return False
        whole, self.waiting = "".join(self.waiting), []
        if self.final and is_json_reply(whole):
            self.out.append(whole)
            self.mode = HEAD
            return False
        self.text, self.mode = whole, HEAD
        return True

    def step_head(self) -> bool:
        if not self.text:
            return False
        newline = self.text.find("\n")
        head = self.text if newline == -1 else self.text[:newline]
        line_known = newline != -1 or self.final
        if self.in_indented_block and starts_indented(head):
            self.mode = INDENTED
            return True
        if self.in_indented_block and may_still_be_indented(head) and not line_known:
            return False
        self.in_indented_block = False
        if opens_a_fence(head):
            self.mode = FENCE_OPENER
        elif may_still_open_a_fence(head) and not line_known:
            return False
        elif self.after_blank_line and starts_indented(head):
            self.mode = INDENTED
        else:
            self.to_prose()
        return True

    def step_indented(self) -> bool:
        newline = self.text.find("\n")
        if newline == -1:
            self.send_raw(len(self.text))
            return False
        self.send_raw(newline + 1)
        self.mode, self.in_indented_block = HEAD, True
        return True

    def step_fence_opener(self) -> bool:
        lead = lead_length(self.text)
        run = run_length(self.text, lead)
        if lead + run == len(self.text) and not self.final:
            return False
        self.fence, self.mode = self.text[lead:lead + run], FENCE_INFO
        return True

    def step_fence_info(self) -> bool:
        newline = self.text.find("\n")
        if newline == -1:
            self.send_raw(len(self.text))
            return False
        self.send_raw(newline + 1)
        self.mode, self.closing_line = FENCE_BODY, ""
        return True

    def step_fence_body(self) -> bool:
        if not self.text:
            return False
        newline = self.text.find("\n")
        line = self.text if newline == -1 else self.text[:newline]
        if self.closing_line is not None:
            self.closing_line += line
            if len(self.closing_line) > LONGEST_CLOSING_FENCE or not set(self.closing_line) <= CLOSING_FENCE_CHARACTERS:
                self.closing_line = None
        if newline == -1:
            self.send_raw(len(self.text))
            return False
        closes = self.closing_line is not None and fence_closes(self.closing_line, self.fence)
        self.send_raw(newline + 1)
        self.mode, self.closing_line = (HEAD, "") if closes else (FENCE_BODY, "")
        return True

    def step_prose(self) -> bool:
        stop = PROSE_STOP.search(self.text, self.cursor)
        if stop is None:
            self.read_prose(len(self.text))
            self.send_prose(len(self.text) if self.final else self.cursor - self.tail)
            return False
        self.read_prose(stop.start())
        found = stop.group()
        if found == "\n":
            self.send_prose(stop.start() + 1)
            self.mode = HEAD
            return True
        self.send_prose(stop.start(), after=found)
        return self.start_inline() if found == "`" else self.start_tag()

    def start_inline(self) -> bool:
        ticks = TICK_RUN.match(self.text).end()
        if ticks == len(self.text):
            if not self.final:
                return False
            self.to_prose(ticks)
            return True
        self.mode, self.ticks, self.waiting, self.inline_next = INLINE, ticks, [self.text[:ticks], self.text[ticks:]], 1
        self.inline_carry, self.inline_base, self.text = "", ticks, ""
        return True

    def step_inline(self) -> bool:
        while self.inline_next < len(self.waiting):
            piece = self.waiting[self.inline_next]
            self.inline_next += 1
            if (found := self.scan_inline(piece, at_end=False)) is not None:
                return self.resolve_inline(found)
        if not self.final:
            return False
        return self.resolve_inline(self.scan_inline("", at_end=True) or -1)

    def scan_inline(self, piece: str, at_end: bool) -> int | None:
        segment, base = self.inline_carry + piece, self.inline_base
        for token in INLINE_STOP.finditer(segment):
            if self.inline_too_long(base + token.start()):
                return -1
            if token.end() == len(segment) and not at_end:
                self.inline_carry, self.inline_base = segment[token.start():], base + token.start()
                return None
            if token.group().startswith("`"):
                if len(token.group()) == self.ticks:
                    return base + token.end()
            elif segment.startswith("\n", token.end()):
                return -1
        if self.inline_too_long(base + len(segment)):
            return -1
        self.inline_carry, self.inline_base = "", base + len(segment)
        return None

    def inline_too_long(self, content_end: int) -> bool:
        return content_end - self.ticks > INLINE_CODE_MAX_CHARS

    def resolve_inline(self, closed_at: int | None) -> bool:
        if closed_at is None:
            return False
        self.text, self.waiting = "".join(self.waiting), []
        if closed_at == -1:
            self.to_prose(self.ticks)
            return True
        self.send_raw(closed_at)
        self.to_prose()
        return True

    def start_tag(self) -> bool:
        self.mode = TAG
        return True

    def step_tag(self) -> bool:
        opener = HTML_OPENER.match(self.text)
        if opener:
            self.html_tag = opener.group(1).lower()
            self.send_raw(opener.end())
            self.mode = HTML
            return True
        if not self.final and HTML_OPENER_SO_FAR.fullmatch(self.text):
            return False
        self.to_prose(1)
        return True

    def step_html(self) -> bool:
        closer = HTML_CLOSERS[self.html_tag].search(self.text)
        if closer:
            self.send_raw(closer.end())
            self.to_prose()
            return True
        last = self.text.rfind("<")
        partial = not self.final and last != -1 and HTML_CLOSERS_SO_FAR[self.html_tag].fullmatch(self.text, last)
        self.send_raw(last if partial else len(self.text))
        return False


def rules_text(rules: ArtyRules, known: str = "") -> private_text.HiddenText:
    return private_text.hidden_text(((RULES_TEXT_LABEL, rules.system),),
                                    known=private_text.KnownText((known,) if known.strip() else ()))


def without_rules_text(text: str, hidden: private_text.HiddenText) -> str:
    return private_text.without_hidden_text(text, hidden)


GUARD_THREADS = 4
GUARD_THREAD_NAME = "rules-guard"
GUARD_CALL_SECONDS = 10.0
guard_executor = ThreadPoolExecutor(max_workers=GUARD_THREADS, thread_name_prefix=GUARD_THREAD_NAME)


async def off_the_loop(function, *args):
    return await asyncio.get_running_loop().run_in_executor(guard_executor, functools.partial(function, *args))


def decline_after(sent: str) -> str:
    return f"\n\n{private_text.DECLINE}" if sent else private_text.DECLINE


class RulesTextGuard(private_text.StreamGuard):
    def cut_off(self) -> str:
        logger.info("held back the rest of a ruled reply that repeated Arty's rules")
        super().cut_off()
        return decline_after(self.sent)

    def calls_repeat(self, calls) -> bool:
        written = "\n".join(text for call in calls if isinstance(call, dict) for text in texts_in(call.get("arguments")))
        return private_text.repeats_hidden_text(written, self.hidden)


class TimedRulesGuard:
    def __init__(self, hidden: private_text.HiddenText):
        self.guard = RulesTextGuard(hidden)
        self.ran_out = False

    @property
    def cut(self) -> bool:
        return self.ran_out or self.guard.cut

    async def within_time(self, function, *args):
        return await asyncio.wait_for(off_the_loop(function, *args), GUARD_CALL_SECONDS)

    def run_out(self) -> str:
        logger.warning("the rules check on a ruled reply took over %g seconds, so the rest of it was held back",
                       GUARD_CALL_SECONDS)
        self.ran_out = True
        return decline_after(self.guard.sent)

    async def read(self, piece: str) -> str:
        if self.ran_out:
            return ""
        try:
            return await self.within_time(self.guard.read, piece)
        except asyncio.TimeoutError:
            return self.run_out()

    async def release(self, finished: bool = False) -> str:
        if self.ran_out:
            return ""
        try:
            return await self.within_time(self.guard.release, finished)
        except asyncio.TimeoutError:
            return self.run_out()

    async def declined_calls(self, calls) -> str:
        if self.cut:
            return ""
        try:
            repeated = await self.within_time(self.guard.calls_repeat, calls)
        except asyncio.TimeoutError:
            return self.run_out()
        return self.guard.cut_off() if repeated else ""


async def without_rules_text_in_events(events, hidden: private_text.HiddenText):
    guard = TimedRulesGuard(hidden)
    try:
        async for kind, item in events:
            if kind == "delta":
                if ready := await guard.read(item):
                    yield kind, ready
                continue
            if kind in TEXT_ENDS and (ready := await guard.release()):
                yield "delta", ready
            if kind == "tool_calls" and (declined := await guard.declined_calls(item)):
                yield "delta", declined
            if kind == "tool_calls" and guard.cut:
                continue
            yield kind, item
        if ready := await guard.release(True):
            yield "delta", ready
    finally:
        await events.aclose()


async def without_rules_text_in_stream(generator, hidden: private_text.HiddenText):
    guard = TimedRulesGuard(hidden)
    try:
        async for chunk in generator:
            for event in sse_events(chunk):
                payload = sse_payload(event)
                if payload is None or not isinstance(payload.get("delta"), str):
                    if ends_the_text(event, payload) and (ready := await guard.release()):
                        yield sse_line({"delta": ready})
                    yield event
                    continue
                ready = await guard.read(payload["delta"])
                others = {name: value for name, value in payload.items() if name != "delta"}
                if ready:
                    yield sse_line({**payload, "delta": ready})
                elif others:
                    yield sse_line(others)
        if ready := await guard.release(True):
            yield sse_line({"delta": ready})
    finally:
        await generator.aclose()


def sse_payload(chunk: bytes) -> dict | None:
    line = chunk.decode("utf-8", errors="replace").strip()
    if not line.startswith(SSE_PREFIX):
        return None
    try:
        payload = json.loads(line[len(SSE_PREFIX):].strip())
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def sse_line(payload: dict) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


def sse_events(chunk: bytes) -> list[bytes]:
    parts = [part for part in chunk.split(SSE_EVENT_END) if part.strip()]
    return [part + SSE_EVENT_END for part in parts] if len(parts) > 1 else [chunk]


def ends_the_text(chunk: bytes, payload: dict | None) -> bool:
    if payload is None:
        return chunk.strip().startswith(SSE_PREFIX.encode())
    return bool(TEXT_ENDS & payload.keys())


def cleaned_event(text: DashlessText, chunk: bytes) -> list[bytes]:
    payload = sse_payload(chunk)
    ends = ends_the_text(chunk, payload)
    if payload is None or not isinstance(payload.get("delta"), str):
        rest = text.finish() if ends else ""
        return ([sse_line({"delta": rest})] if rest else []) + [chunk]
    cleaned = text.add(payload["delta"]) + (text.finish() if ends else "")
    others = {name: value for name, value in payload.items() if name != "delta"}
    if cleaned:
        return [sse_line({**payload, "delta": cleaned})]
    return [sse_line(others)] if others else []


async def without_em_dashes_in_stream(generator):
    text = DashlessText()
    try:
        async for chunk in generator:
            for event in sse_events(chunk):
                for cleaned in cleaned_event(text, event):
                    yield cleaned
        if rest := text.finish():
            yield sse_line({"delta": rest})
    finally:
        await generator.aclose()


async def without_em_dashes_in_events(events):
    text = DashlessText()
    try:
        async for kind, item in events:
            if kind == "delta":
                if cleaned := text.add(item):
                    yield kind, cleaned
                continue
            if kind in TEXT_ENDS and (rest := text.finish()):
                yield "delta", rest
            yield kind, item
        if rest := text.finish():
            yield "delta", rest
    finally:
        await events.aclose()
