import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import httpx
import pytest

import model_endpoints
import ollama_manager

ENGINE_TAGS = (200, [("Server", "arty-engine/0.1.0 Python/3.13.5")], '{"models": []}')
OLLAMA_TAGS = (200, [], '{"models": []}')


@pytest.fixture
def machine(monkeypatch):
    answers = {}
    launched = []

    def handle(request):
        reply = answers.get(f"{request.url.host}:{request.url.port}{request.url.path}")
        if reply is None:
            raise httpx.ConnectError("connection refused", request=request)
        status, headers, text = reply
        return httpx.Response(status, headers=headers, text=text)

    async def launch(*command, **_options):
        launched.append(command)
        answers["127.0.0.1:11434/api/tags"] = OLLAMA_TAGS
        answers["localhost:11434/api/tags"] = OLLAMA_TAGS

    real_client = httpx.AsyncClient
    monkeypatch.setattr(model_endpoints.httpx, "AsyncClient",
                        lambda *args, **kwargs: real_client(*args, transport=httpx.MockTransport(handle), **kwargs))
    monkeypatch.setattr(ollama_manager, "is_installed", lambda: True)
    monkeypatch.setattr(ollama_manager.asyncio, "create_subprocess_exec", launch)
    return answers, launched


def test_ollama_is_started_even_when_another_backend_answers_on_the_engine_port(machine):
    answers, launched = machine
    answers["127.0.0.1:11438/api/tags"] = ENGINE_TAGS
    assert asyncio.run(ollama_manager.start(None)) == (True, "Started.")
    assert launched == [("ollama", "serve")]


def test_a_running_ollama_is_not_started_again(machine):
    answers, launched = machine
    answers["localhost:11434/api/tags"] = OLLAMA_TAGS
    assert asyncio.run(ollama_manager.start(None)) == (True, "Already running.")
    assert launched == []


def test_a_configured_address_that_answers_counts_as_running(machine):
    answers, launched = machine
    answers["gpu-box:11434/api/tags"] = OLLAMA_TAGS
    assert asyncio.run(ollama_manager.start("http://gpu-box:11434/api")) == (True, "Already running.")
    assert launched == []


@pytest.mark.parametrize("address", ["http://gpu-box:11434", "http://127.0.0.1:11500", "http://localhost:11438/api"])
def test_ollama_is_not_launched_when_the_server_starfire_uses_is_elsewhere_and_down(machine, address):
    _, launched = machine
    ok, message = asyncio.run(ollama_manager.start(address))
    assert ok is False
    assert address in message and "not answering" in message
    assert launched == []


@pytest.mark.parametrize("address", ["http://localhost:11434", "http://127.0.0.1:11434/api"])
def test_ollama_is_launched_when_the_server_starfire_uses_is_its_own_port_here(machine, address):
    _, launched = machine
    asyncio.run(ollama_manager.start(address))
    assert launched == [("ollama", "serve")]


def test_startup_does_not_launch_ollama_for_a_stored_server_on_another_port(machine, tmp_path):
    _, launched = machine
    store = model_endpoints.ModelEndpointStore(str(tmp_path))
    store.add("http://127.0.0.1:11500", "ollama")

    async def start_installed_ollama(address=None):
        started, _ = await ollama_manager.start(address)
        return started

    assert asyncio.run(model_endpoints.settle_local_backend(store, None, start_installed_ollama)) is None
    assert launched == []
    assert [e.base_url for e in store.list()] == ["http://127.0.0.1:11500"]


def test_startup_launches_ollama_for_a_stored_ollama_on_its_own_port_that_is_down(machine, tmp_path):
    _, launched = machine
    store = model_endpoints.ModelEndpointStore(str(tmp_path))
    store.add("http://127.0.0.1:11434", "ollama", label="Ollama (auto-detected)")

    async def start_installed_ollama(address=None):
        started, _ = await ollama_manager.start(address)
        return started

    asyncio.run(model_endpoints.settle_local_backend(store, None, start_installed_ollama))
    assert launched == [("ollama", "serve")]
