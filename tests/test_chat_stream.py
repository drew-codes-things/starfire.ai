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
