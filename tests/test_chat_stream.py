import asyncio
import json
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import httpx
import pytest

import chat_stream


def read_stream(iterator, body: str):
    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body))
        async with httpx.AsyncClient(transport=transport) as client:
            return [item async for item in iterator(client, "http://model/chat", {}, {})]

    return asyncio.run(run())


def read_refusal(iterator, status: int, body: str):
    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(status, text=body))
        async with httpx.AsyncClient(transport=transport) as client:
            return [item async for item in iterator(client, "http://model/chat", {}, {})]

    return asyncio.run(run())


def test_a_refused_openai_request_shows_the_message_not_the_json_around_it():
    body = '{"error": {"message": "the model is overloaded", "type": "server_error"}}'
    assert read_refusal(chat_stream._iter_openai, 500, body) == [("error", "500: the model is overloaded")]


def test_a_refused_ollama_request_shows_the_message_not_the_json_around_it():
    body = '{"error":"model \'tiny\' not found"}'
    assert read_refusal(chat_stream._iter_ollama, 404, body) == [("error", "Ollama 404: model 'tiny' not found")]


def test_every_provider_shows_a_refusals_message_not_the_json_around_it():
    readers = [getattr(chat_stream, name) for name in dir(chat_stream) if name.startswith("_iter_")]
    assert len(readers) >= 3
    body = '{"type":"error","error":{"type":"authentication_error","message":"that key is not valid"}}'
    for reader in readers:
        [(kind, text)] = read_refusal(reader, 401, body)
        assert kind == "error" and text.endswith("401: that key is not valid"), text


def test_a_refusal_that_is_not_json_is_shown_as_it_came_and_cut_short():
    assert read_refusal(chat_stream._iter_openai, 502, "<html>Bad Gateway</html>") == [
        ("error", "502: <html>Bad Gateway</html>")]
    [(kind, text)] = read_refusal(chat_stream._iter_openai, 502, "x" * 5000)
    assert kind == "error" and len(text) == len("502: ") + chat_stream.MAX_ERROR_CHARS


def test_a_refusal_with_nothing_in_it_still_says_something():
    assert read_refusal(chat_stream._iter_openai, 503, "") == [("error", "503: the model server gave no details")]
    assert read_refusal(chat_stream._iter_openai, 500, '{"error": {}}') == [("error", '500: {"error": {}}')]


def test_an_ollama_error_inside_a_working_stream_is_reported():
    body = '{"error":"model requires more system memory than is available"}\n'
    assert read_stream(chat_stream._iter_ollama, body) == [
        ("error", "Ollama: model requires more system memory than is available")]


def test_an_ollama_error_after_some_text_keeps_the_text_and_reports_the_error():
    body = '{"message":{"content":"Hel"}}\n{"error":"connection to the runner was lost"}\n'
    assert read_stream(chat_stream._iter_ollama, body) == [
        ("delta", "Hel"), ("error", "Ollama: connection to the runner was lost")]


def test_an_openai_style_error_object_inside_the_stream_is_reported():
    body = 'data: {"error": {"message": "context length exceeded", "type": "invalid_request_error"}}\n\n'
    assert read_stream(chat_stream._iter_openai, body) == [("error", "context length exceeded")]
    assert read_stream(chat_stream._iter_openai, 'data: {"error": "upstream timed out"}\n\n') == [
        ("error", "upstream timed out")]


def test_a_normal_openai_stream_is_unchanged():
    body = 'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\ndata: [DONE]\n\n'
    assert read_stream(chat_stream._iter_openai, body) == [("delta", "Hi")]


OPENAI_MISSHAPEN_LINES = [
    '"just a string"',
    '{"choices": ["not an object"]}',
    '{"choices": "not a list"}',
    '{"choices": [{"index": 0, "delta": "not an object"}]}',
    '{"choices": [{"index": 0, "delta": {"content": 7}}]}',
    '{"choices": [{"index": 0, "delta": {"tool_calls": ["not an object"]}}]}',
    '{"choices": [{"index": 0, "delta": {"tool_calls": [{"index": [0], "function": "not an object"}]}}]}',
    '{"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": 5}}]}}]}',
]
LOCAL_RUNNER_MISSHAPEN_LINES = [
    '"just a string"',
    '{"message": "not an object"}',
    '{"message": {"content": ["not", "text"]}}',
    '{"message": {"tool_calls": ["not an object", 5]}}',
]
HOSTED_MISSHAPEN_EVENTS = [
    ("content_block_start", '"just a string"'),
    ("content_block_start", '{"index": [1], "content_block": "not an object"}'),
    ("content_block_delta", '{"index": 0, "delta": "not an object"}'),
    ("content_block_delta", '{"index": {}, "delta": {"type": "text_delta", "text": 3}}'),
    ("message_delta", '{"usage": "none", "delta": "not an object"}'),
]


def test_misshapen_openai_lines_are_skipped_like_unreadable_ones():
    lines = [f"data: {line}\n\n" for line in OPENAI_MISSHAPEN_LINES]
    body = "".join(lines) + 'data: {"choices": [{"index": 0, "delta": {"content": "hi"}}]}\n\ndata: [DONE]\n\n'
    assert read_stream(chat_stream._iter_openai, body) == [("delta", "hi")]


def test_misshapen_local_runner_lines_are_skipped_like_unreadable_ones():
    body = "\n".join(LOCAL_RUNNER_MISSHAPEN_LINES + ['{"message": {"content": "hi"}}', '{"done": true}']) + "\n"
    assert read_stream(chat_stream._iter_ollama, body) == [("delta", "hi")]


def test_misshapen_hosted_events_are_skipped_like_unreadable_ones():
    events = [f"event: {kind}\ndata: {data}\n\n" for kind, data in HOSTED_MISSHAPEN_EVENTS]
    good = 'event: content_block_delta\ndata: {"index": 0, "delta": {"type": "text_delta", "text": "hi"}}\n\n'
    body = "".join(events) + good + 'event: message_stop\ndata: {}\n\n'
    assert read_stream(chat_stream._iter_anthropic, body) == [("delta", "hi")]


def openai_lines(*chunks) -> str:
    return "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"


def openai_choice(delta, finish=None):
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def openai_call(index, call_id, name, arguments):
    return openai_choice({"tool_calls": [{"index": index, "id": call_id,
                                          "function": {"name": name, "arguments": arguments}}]})


TWO_CALLS = [("tool_calls", [{"id": "a", "name": "search_web", "arguments": {"q": "x"}},
                             {"id": "b", "name": "find_image", "arguments": {"q": "y"}}])]


@pytest.mark.parametrize("first, second", [("0", "1"), (0.0, 1.0)], ids=["digit strings", "whole floats"])
def test_tool_call_indexes_sent_as_digits_or_whole_floats_keep_the_calls_apart(first, second):
    body = openai_lines(openai_call(first, "a", "search_web", '{"q": "x"}'),
                        openai_call(second, "b", "find_image", '{"q": "y"}'), openai_choice({}, "tool_calls"))
    assert read_stream(chat_stream._iter_openai, body) == TWO_CALLS


def test_typed_event_tool_blocks_with_digit_string_indexes_keep_the_calls_apart():
    events = [("content_block_start", {"index": "0", "content_block": {"type": "tool_use", "id": "a",
                                                                         "name": "search_web"}}),
              ("content_block_delta", {"index": "0", "delta": {"type": "input_json_delta",
                                                                 "partial_json": '{"q": "x"}'}}),
              ("content_block_start", {"index": "1", "content_block": {"type": "tool_use", "id": "b",
                                                                         "name": "find_image"}}),
              ("content_block_delta", {"index": "1", "delta": {"type": "input_json_delta",
                                                                 "partial_json": '{"q": "y"}'}}),
              ("message_delta", {"delta": {"stop_reason": "tool_use"}}), ("message_stop", {})]
    body = "".join(f"event: {kind}\ndata: {json.dumps(data)}\n\n" for kind, data in events)
    assert read_stream(chat_stream._iter_anthropic, body) == TWO_CALLS


def test_tool_call_arguments_sent_as_an_object_are_kept():
    body = openai_lines(openai_call(0, "a", "search_web", {"q": "x"}), openai_choice({}, "tool_calls"))
    assert read_stream(chat_stream._iter_openai, body) == [("tool_calls", [{"id": "a", "name": "search_web",
                                                                             "arguments": {"q": "x"}}])]


def test_a_tool_call_id_sent_as_a_number_is_kept_as_text():
    body = openai_lines(openai_call(0, 7, "search_web", '{"q": "x"}'), openai_choice({}, "tool_calls"))
    assert read_stream(chat_stream._iter_openai, body)[0][1][0]["id"] == "7"


TEXT_PARTS = [{"type": "text", "text": "Hel"}, {"type": "image_url", "image_url": {"url": "x"}},
              {"type": "text", "text": "lo"}]
THINKING_PARTS = [{"type": "thinking", "thinking": [{"type": "text", "text": "hm"}]}]


def test_openai_content_sent_as_text_parts_comes_through_and_thinking_parts_stay_out():
    body = openai_lines(openai_choice({"content": THINKING_PARTS}), openai_choice({"content": TEXT_PARTS}),
                        openai_choice({}, "stop"))
    assert read_stream(chat_stream._iter_openai, body) == [("delta", "Hello")]


def test_local_runner_content_sent_as_text_parts_comes_through():
    body = "\n".join(json.dumps(line) for line in ({"message": {"content": TEXT_PARTS}}, {"done": True})) + "\n"
    assert read_stream(chat_stream._iter_ollama, body) == [("delta", "Hello")]


def test_a_skipped_field_is_logged_once_per_reply(caplog):
    body = openai_lines({"choices": ["not an object"]}, openai_choice({"content": 42}),
                        openai_choice({"content": "hi"}), openai_choice({}, "stop"))
    with caplog.at_level(logging.WARNING, logger="chat_stream"):
        assert read_stream(chat_stream._iter_openai, body) == [("delta", "hi")]
        read_stream(chat_stream._iter_openai, body)
    skipped = [record.getMessage() for record in caplog.records if "skipped" in record.getMessage()]
    assert skipped == ["OpenAI-style reply: skipped the choice because it was sent as str; later skips in this reply "
                       "are not logged"] * 2


def test_empty_object_arguments_before_string_pieces_keep_the_real_arguments():
    body = openai_lines(openai_call(0, "a", "search_web", {}), openai_call(0, None, None, '{"q": '),
                        openai_call(0, None, None, '"x"}'), openai_choice({}, "tool_calls"))
    assert read_stream(chat_stream._iter_openai, body) == [("tool_calls", [{"id": "a", "name": "search_web",
                                                                             "arguments": {"q": "x"}}])]


def test_empty_object_arguments_alone_still_mean_no_arguments():
    body = openai_lines(openai_call(0, "a", "search_web", {}), openai_choice({}, "tool_calls"))
    assert read_stream(chat_stream._iter_openai, body) == [("tool_calls", [{"id": "a", "name": "search_web",
                                                                             "arguments": {}}])]


HUGE_DIGIT_STRING = "1" + "0" * 5000


def test_an_index_with_too_many_digits_is_skipped_not_a_crash():
    body = openai_lines(openai_call(HUGE_DIGIT_STRING, "a", "search_web", '{"q": "x"}'),
                        openai_choice({}, "tool_calls"))
    assert read_stream(chat_stream._iter_openai, body) == [("tool_calls", [{"id": "a", "name": "search_web",
                                                                             "arguments": {"q": "x"}}])]


def test_a_typed_event_index_with_too_many_digits_is_skipped_not_a_crash():
    events = [("content_block_start", {"index": HUGE_DIGIT_STRING, "content_block": {"type": "tool_use", "id": "a",
                                                                                     "name": "search_web"}}),
              ("content_block_delta", {"index": HUGE_DIGIT_STRING, "delta": {"type": "input_json_delta",
                                                                             "partial_json": '{"q": "x"}'}}),
              ("message_delta", {"delta": {"stop_reason": "tool_use"}}), ("message_stop", {})]
    body = "".join(f"event: {kind}\ndata: {json.dumps(data)}\n\n" for kind, data in events)
    assert read_stream(chat_stream._iter_anthropic, body) == [("tool_calls", [{"id": "a", "name": "search_web",
                                                                                "arguments": {"q": "x"}}])]


HUGE_NUMBER = "1" + "0" * 5000
HUGE_ARGUMENTS = json.dumps('{"n": ' + HUGE_NUMBER + "}")
OPENAI_GOOD_TEXT = 'data: {"choices": [{"index": 0, "delta": {"content": "hi"}}]}\n\n'
NDJSON_GOOD_TEXT = '{"message": {"content": "hi"}}\n{"done": true}\n'
TYPED_GOOD_TEXT = ('event: content_block_delta\ndata: {"index": 0, "delta": {"type": "text_delta", "text": "hi"}}\n\n'
                   "event: message_stop\ndata: {}\n\n")
HUGE_NUMBER_LINES = {
    "OpenAI-style index": (chat_stream._iter_openai, 'data: {"choices": [{"index": ' + HUGE_NUMBER
                           + ', "delta": {"content": "lost"}}]}\n\n' + OPENAI_GOOD_TEXT + "data: [DONE]\n\n"),
    "OpenAI-style unrelated field": (chat_stream._iter_openai, 'data: {"created": ' + HUGE_NUMBER
                                     + ', "choices": [{"index": 0, "delta": {"content": "lost"}}]}\n\n'
                                     + OPENAI_GOOD_TEXT + "data: [DONE]\n\n"),
    "NDJSON tool call index": (chat_stream._iter_ollama, '{"message": {"tool_calls": [{"function": {"index": '
                               + HUGE_NUMBER + ', "name": "lost"}}]}}\n' + NDJSON_GOOD_TEXT),
    "NDJSON unrelated field": (chat_stream._iter_ollama, '{"created_at": ' + HUGE_NUMBER
                               + ', "message": {"content": "lost"}}\n' + NDJSON_GOOD_TEXT),
    "typed-event index": (chat_stream._iter_anthropic, "event: content_block_delta\ndata: {\"index\": " + HUGE_NUMBER
                          + ', "delta": {"type": "text_delta", "text": "lost"}}\n\n' + TYPED_GOOD_TEXT),
    "typed-event unrelated field": (chat_stream._iter_anthropic, "event: content_block_delta\ndata: {\"created\": "
                                    + HUGE_NUMBER + ', "index": 0, "delta": {"type": "text_delta", "text": "lost"}}\n\n'
                                    + TYPED_GOOD_TEXT),
}


@pytest.mark.parametrize("where", HUGE_NUMBER_LINES.keys())
def test_a_line_holding_a_number_too_long_to_convert_is_skipped_not_a_crash(where):
    reader, body = HUGE_NUMBER_LINES[where]
    assert read_stream(reader, body) == [("delta", "hi")]


NO_ARGUMENTS_CALL = [("tool_calls", [{"id": "a", "name": "search_web", "arguments": {}}])]
HUGE_ARGUMENT_REPLIES = {
    "OpenAI-style": (chat_stream._iter_openai, 'data: {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, '
                     '"id": "a", "function": {"name": "search_web", "arguments": ' + HUGE_ARGUMENTS + '}}]}}]}\n\n'
                     'data: {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}\n\n'
                     "data: [DONE]\n\n"),
    "NDJSON": (chat_stream._iter_ollama, '{"message": {"tool_calls": [{"id": "a", "function": {"name": "search_web", '
               '"arguments": ' + HUGE_ARGUMENTS + '}}]}}\n{"done": true}\n'),
    "typed-event": (chat_stream._iter_anthropic, 'event: content_block_start\ndata: {"index": 0, "content_block": '
                    '{"type": "tool_use", "id": "a", "name": "search_web"}}\n\nevent: content_block_delta\ndata: '
                    '{"index": 0, "delta": {"type": "input_json_delta", "partial_json": ' + HUGE_ARGUMENTS + '}}\n\n'
                    'event: message_delta\ndata: {"delta": {"stop_reason": "tool_use"}}\n\n'
                    "event: message_stop\ndata: {}\n\n"),
}


@pytest.mark.parametrize("reader", HUGE_ARGUMENT_REPLIES.keys())
def test_tool_arguments_holding_a_number_too_long_to_convert_become_no_arguments(reader):
    iterator, body = HUGE_ARGUMENT_REPLIES[reader]
    assert read_stream(iterator, body) == NO_ARGUMENTS_CALL


HISTORY_WITH_HUGE_ARGUMENTS = [{"role": "assistant", "content": "", "tool_calls": [
    {"id": "a", "function": {"name": "search_web", "arguments": '{"n": ' + HUGE_NUMBER + "}"}}]}]


def test_earlier_tool_arguments_holding_a_number_too_long_to_convert_are_sent_as_empty():
    typed = chat_stream._to_anthropic_messages(HISTORY_WITH_HUGE_ARGUMENTS)
    per_line = chat_stream._to_ollama_messages(HISTORY_WITH_HUGE_ARGUMENTS)
    assert typed[0]["content"][0]["input"] == {} and per_line[0]["tool_calls"][0]["function"]["arguments"] == {}


DEEP_JSON = "[" * 100_000 + "]" * 100_000
UNSENDABLE_ARGUMENTS = {"infinite": '{"n": 1e999}', "not a number": '{"n": NaN}', "nested too deep": DEEP_JSON}


def openai_call_reply(arguments: str) -> str:
    return openai_lines(openai_call(0, "a", "search_web", arguments), openai_choice({}, "tool_calls"))


def per_line_call_reply(arguments) -> str:
    call = {"message": {"tool_calls": [{"id": "a", "function": {"name": "search_web", "arguments": "ARGS"}}]}}
    line = json.dumps(call).replace('"ARGS"', arguments)
    return line + '\n{"done": true}\n'


def typed_call_reply(arguments: str) -> str:
    return ('event: content_block_start\ndata: {"index": 0, "content_block": {"type": "tool_use", "id": "a", '
            '"name": "search_web"}}\n\nevent: content_block_delta\ndata: '
            + json.dumps({"index": 0, "delta": {"type": "input_json_delta", "partial_json": arguments}})
            + '\n\nevent: message_delta\ndata: {"delta": {"stop_reason": "tool_use"}}\n\n'
            'event: message_stop\ndata: {}\n\n')


def per_line_string_call_reply(arguments: str) -> str:
    return per_line_call_reply(json.dumps(arguments))


@pytest.mark.parametrize("arguments", UNSENDABLE_ARGUMENTS.keys())
@pytest.mark.parametrize("reader, reply", [(chat_stream._iter_openai, openai_call_reply),
                                           (chat_stream._iter_ollama, per_line_string_call_reply),
                                           (chat_stream._iter_anthropic, typed_call_reply)],
                         ids=["OpenAI-style", "NDJSON string", "typed-event"])
def test_tool_arguments_that_could_not_be_sent_back_become_no_arguments(reader, reply, arguments):
    assert read_stream(reader, reply(UNSENDABLE_ARGUMENTS[arguments])) == NO_ARGUMENTS_CALL


@pytest.mark.parametrize("arguments", ['{"n": 1e999}', '{"n": NaN}', '{"n": -Infinity}'])
def test_per_line_tool_arguments_object_holding_a_non_finite_number_becomes_no_arguments(arguments):
    assert read_stream(chat_stream._iter_ollama, per_line_call_reply(arguments)) == NO_ARGUMENTS_CALL


@pytest.mark.parametrize("arguments", UNSENDABLE_ARGUMENTS.keys())
def test_earlier_tool_arguments_that_could_not_be_sent_are_sent_as_empty(arguments):
    history = [{"role": "assistant", "content": "", "tool_calls": [
        {"id": "a", "function": {"name": "search_web", "arguments": UNSENDABLE_ARGUMENTS[arguments]}}]}]
    typed = chat_stream._to_anthropic_messages(history)
    per_line = chat_stream._to_ollama_messages(history)
    json.dumps([typed, per_line], allow_nan=False)
    assert typed[0]["content"][0]["input"] == {} and per_line[0]["tool_calls"][0]["function"]["arguments"] == {}


NAMELESS_CALL_REPLIES = {
    "OpenAI-style": (chat_stream._iter_openai, openai_lines(
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "x", "function": None}]}}]},
        openai_call(1, "a", "search_web", ""), openai_choice({}, "tool_calls"))),
    "NDJSON": (chat_stream._iter_ollama, '{"message": {"tool_calls": [{"id": "x"}, {"id": "a", "function": '
               '{"name": "search_web", "arguments": {}}}]}}\n{"done": true}\n'),
    "typed-event": (chat_stream._iter_anthropic, 'event: content_block_start\ndata: {"index": 0, "content_block": '
                    '{"type": "tool_use", "id": "x"}}\n\nevent: content_block_start\ndata: {"index": 1, '
                    '"content_block": {"type": "tool_use", "id": "a", "name": "search_web"}}\n\n'
                    'event: message_delta\ndata: {"delta": {"stop_reason": "tool_use"}}\n\n'
                    "event: message_stop\ndata: {}\n\n"),
}


@pytest.mark.parametrize("reader", NAMELESS_CALL_REPLIES.keys())
def test_a_tool_call_with_no_name_is_dropped_and_the_named_one_kept(reader, caplog):
    iterator, body = NAMELESS_CALL_REPLIES[reader]
    with caplog.at_level(logging.WARNING, logger="chat_stream"):
        assert read_stream(iterator, body) == NO_ARGUMENTS_CALL
    assert any("dropped a tool call with no name" in record.getMessage() for record in caplog.records)


DEEP_LINES = {
    "OpenAI-style": (chat_stream._iter_openai, f"data: {DEEP_JSON}\n\n" + OPENAI_GOOD_TEXT + "data: [DONE]\n\n"),
    "NDJSON": (chat_stream._iter_ollama, DEEP_JSON + "\n" + NDJSON_GOOD_TEXT),
    "typed-event": (chat_stream._iter_anthropic,
                    f"event: content_block_delta\ndata: {DEEP_JSON}\n\n" + TYPED_GOOD_TEXT),
}


@pytest.mark.parametrize("reader", DEEP_LINES.keys())
def test_a_line_nested_too_deep_to_read_is_skipped_not_a_crash(reader):
    iterator, body = DEEP_LINES[reader]
    assert read_stream(iterator, body) == [("delta", "hi")]


def test_a_refusal_nested_too_deep_to_read_is_shown_as_text():
    assert read_refusal(chat_stream._iter_openai, 500, DEEP_JSON)[0][1].startswith("500: [[[")


OUT_OF_RANGE_USAGE = {
    "OpenAI-style infinite tokens": (chat_stream._iter_openai, openai_lines(
        openai_choice({"content": "hi"}), openai_choice({}, "stop"),
        {"choices": [], "usage": {"prompt_tokens": "INF", "completion_tokens": 3}}).replace('"INF"', "1e999")),
    "OpenAI-style NaN tokens": (chat_stream._iter_openai, openai_lines(
        openai_choice({"content": "hi"}), openai_choice({}, "stop"),
        {"choices": [], "usage": {"prompt_tokens": "INF", "completion_tokens": 3}}).replace('"INF"', "NaN")),
    "OpenAI-style 400-digit milliseconds": (chat_stream._iter_openai, openai_lines(
        openai_choice({"content": "hi"}), openai_choice({}, "stop"),
        {"choices": [], "usage": {"completion_tokens": 3},
         "timings": {"predicted_ms": "HUGE"}}).replace('"HUGE"', "9" * 400)),
    "NDJSON NaN counts": (chat_stream._iter_ollama, '{"message": {"content": "hi"}}\n'
                          '{"done": true, "prompt_eval_count": NaN, "eval_count": 3}\n'),
    "NDJSON 400-digit duration": (chat_stream._iter_ollama, '{"message": {"content": "hi"}}\n'
                                  '{"done": true, "eval_count": 3, "eval_duration": ' + "9" * 400 + "}\n"),
    "typed-event infinite tokens": (chat_stream._iter_anthropic, TYPED_GOOD_TEXT.replace(
        "event: message_stop", 'event: message_start\ndata: {"message": {"usage": {"input_tokens": 1e999}}}\n\n'
        'event: message_delta\ndata: {"usage": {"output_tokens": 3}}\n\nevent: message_stop')),
}


@pytest.mark.parametrize("where", OUT_OF_RANGE_USAGE.keys())
def test_out_of_range_usage_figures_are_left_out_not_a_crash(where):
    iterator, body = OUT_OF_RANGE_USAGE[where]
    assert read_stream(iterator, body) == [("delta", "hi"), ("usage", {"completion_tokens": 3})]


NOT_AN_OBJECT = {"list": "[1, 2]", "text": '"x"', "number": "5", "null": "null"}


@pytest.mark.parametrize("arguments", NOT_AN_OBJECT.keys())
@pytest.mark.parametrize("reader, reply", [(chat_stream._iter_openai, openai_call_reply),
                                           (chat_stream._iter_ollama, per_line_string_call_reply),
                                           (chat_stream._iter_anthropic, typed_call_reply)],
                         ids=["OpenAI-style", "NDJSON string", "typed-event"])
def test_tool_arguments_that_are_not_an_object_become_no_arguments(reader, reply, arguments):
    assert read_stream(reader, reply(NOT_AN_OBJECT[arguments])) == NO_ARGUMENTS_CALL


@pytest.mark.parametrize("arguments", ["[1, 2]", '"x"', "5", [1, 2], 5, "text that is not JSON"])
def test_earlier_tool_arguments_that_are_not_an_object_are_sent_as_empty(arguments):
    history = [{"role": "assistant", "content": "", "tool_calls": [
        {"id": "a", "function": {"name": "search_web", "arguments": arguments}}]}]
    typed = chat_stream._to_anthropic_messages(history)
    per_line = chat_stream._to_ollama_messages(history)
    assert typed[0]["content"][0]["input"] == {} and per_line[0]["tool_calls"][0]["function"]["arguments"] == {}


LONG_COUNT = "9" * 4300
COUNTS_TOO_LARGE = {
    "OpenAI-style timed prompt and cache": (chat_stream._iter_openai, openai_lines(
        openai_choice({"content": "hi"}), openai_choice({}, "stop"),
        {"choices": [], "usage": {"completion_tokens": 3}, "timings": {"prompt_n": "LONG", "cache_n": "LONG"}})),
    "OpenAI-style one count past the cap": (chat_stream._iter_openai, openai_lines(
        openai_choice({"content": "hi"}), openai_choice({}, "stop"),
        {"choices": [], "usage": {"prompt_tokens": 10**12 + 1, "completion_tokens": 3}})),
    "NDJSON prompt count": (chat_stream._iter_ollama, '{"message": {"content": "hi"}}\n'
                            '{"done": true, "prompt_eval_count": LONG, "eval_count": 3}\n'),
    "typed-event fresh and cached prompt": (chat_stream._iter_anthropic, TYPED_GOOD_TEXT.replace(
        "event: message_stop", 'event: message_start\ndata: {"message": {"usage": {"input_tokens": LONG, '
        '"cache_read_input_tokens": LONG}}}\n\nevent: message_delta\ndata: {"usage": {"output_tokens": 3}}\n\n'
        "event: message_stop")),
}


@pytest.mark.parametrize("where", COUNTS_TOO_LARGE.keys())
def test_token_counts_past_the_cap_are_left_out_so_the_usage_line_can_be_written(where):
    iterator, body = COUNTS_TOO_LARGE[where]
    read = read_stream(iterator, body.replace('"LONG"', LONG_COUNT).replace("LONG", LONG_COUNT))
    json.dumps(read)
    assert read == [("delta", "hi"), ("usage", {"completion_tokens": 3})]
