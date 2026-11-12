import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import httpx

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
