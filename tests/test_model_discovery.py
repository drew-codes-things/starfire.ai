import asyncio
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import httpx
import pytest

import model_endpoints
from model_endpoints import ModelEndpointStore


@pytest.fixture
def local_ports(monkeypatch):
    answers = {}
    asked = []

    def handle(request):
        address = f"{request.url.host}:{request.url.port}"
        asked.append(f"{address}{request.url.path}")
        reply = answers.get(f"{address}{request.url.path}", answers.get(address, "dead"))
        if reply == "dead":
            raise httpx.ConnectError("connection refused", request=request)
        if reply == "hangs":
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(reply)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(model_endpoints.httpx, "AsyncClient",
                        lambda *args, **kwargs: real_client(*args, transport=httpx.MockTransport(handle), **kwargs))
    return answers, asked


def test_a_configured_ollama_address_is_used_without_probing(local_ports):
    _, asked = local_ports
    assert asyncio.run(model_endpoints.detect_ollama("http://gpu-box:11434")) == "http://gpu-box:11434"
    assert asked == []


def test_ollama_on_this_machine_is_found(local_ports):
    answers, _ = local_ports
    answers["localhost:11434/api/tags"] = 200
    assert asyncio.run(model_endpoints.detect_ollama(None)) == "http://localhost:11434"


@pytest.mark.parametrize("reply", ["dead", "hangs", 500])
def test_no_ollama_is_reported_when_nothing_healthy_answers(local_ports, reply):
    answers, asked = local_ports
    answers["127.0.0.1:11434"] = reply
    answers["localhost:11434"] = reply
    assert asyncio.run(model_endpoints.detect_ollama(None)) is None
    assert asked == ["127.0.0.1:11434/api/tags", "localhost:11434/api/tags"]


def test_discovery_lists_each_running_server_once_and_skips_dead_and_hanging_ports(local_ports):
    answers, _ = local_ports
    answers["127.0.0.1:11434/api/tags"] = 200
    answers["localhost:11434/api/tags"] = 200
    answers["127.0.0.1:1234/api/tags"] = 404
    answers["127.0.0.1:1234/v1/models"] = 200
    answers["127.0.0.1:8080"] = "hangs"
    found = asyncio.run(model_endpoints.discover_servers())
    assert found == [
        {"base_url": "http://127.0.0.1:11434", "kind": "ollama", "label": "Ollama (127.0.0.1:11434)"},
        {"base_url": "http://127.0.0.1:1234", "kind": "openai-compatible", "label": "Local server (127.0.0.1:1234)"},
    ]


def test_discovery_finds_nothing_when_nothing_is_running(local_ports):
    _, asked = local_ports
    assert asyncio.run(model_endpoints.discover_servers()) == []
    assert len(asked) == len(model_endpoints.LOCAL_HOSTS) * len(model_endpoints.DISCOVERY_PORTS) * 2


def test_a_server_that_errors_on_both_apis_is_not_offered(local_ports):
    answers, _ = local_ports
    answers["127.0.0.1:8080"] = 500
    answers["localhost:8080"] = 500
    assert asyncio.run(model_endpoints.discover_servers()) == []


def write_endpoints(tmp_path, raw):
    (tmp_path / "endpoints.json").write_text(json.dumps(raw))
    return ModelEndpointStore(str(tmp_path))


def test_broken_entries_in_the_endpoints_file_are_skipped_and_the_rest_still_load(tmp_path):
    store = write_endpoints(tmp_path, [
        "not an endpoint",
        {"id": "no-url"},
        {"base_url": "http://no-id"},
        {"id": "ok", "base_url": "https://api.openai.com/v1"},
    ])
    [endpoint] = store.list()
    assert endpoint.id == "ok"
    assert endpoint.kind == "api-key"
    assert endpoint.provider == "openai"
    assert endpoint.model_type == "chat"


def test_an_endpoints_file_that_is_not_a_list_reads_as_empty_and_can_be_added_to(tmp_path):
    store = write_endpoints(tmp_path, {"id": "ok", "base_url": "http://x"})
    assert store.list() == []
    added = store.add("http://127.0.0.1:11434/", "ollama", label="Local")
    assert [e.base_url for e in store.list()] == ["http://127.0.0.1:11434"]
    assert store.find_by_url("http://127.0.0.1:11434/").id == added.id
