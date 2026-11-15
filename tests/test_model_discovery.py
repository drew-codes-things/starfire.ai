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
        if isinstance(reply, tuple):
            status, headers, text = reply
            return httpx.Response(status, headers=headers, text=text)
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
    answers["localhost:11434/api/tags"] = OLLAMA_TAGS
    assert asyncio.run(model_endpoints.detect_ollama(None)) == "http://localhost:11434"


@pytest.mark.parametrize("reply", ["dead", "hangs", 500])
def test_no_ollama_is_reported_when_nothing_healthy_answers(local_ports, reply):
    answers, asked = local_ports
    for address in ("127.0.0.1:11438", "localhost:11438", "127.0.0.1:11434", "localhost:11434"):
        answers[address] = reply
    assert asyncio.run(model_endpoints.detect_ollama(None)) is None
    assert asked == ["127.0.0.1:11438/api/tags", "localhost:11438/api/tags",
                     "127.0.0.1:11434/api/tags", "localhost:11434/api/tags"]


ENGINE_BEHIND_CADDY = (200, [("Server", "Caddy"), ("Server", "arty-engine/0.1.0 Python/3.13.5")], '{"models": []}')
ENGINE_TAGS = (200, [("Server", "arty-engine/0.1.0 Python/3.13.5")], '{"models": []}')
OLLAMA_TAGS = (200, [], '{"models": []}')
ANSWERS_EVERY_PATH = (200, [], '{"error": "Unexpected endpoint or method. Returning 200 anyway"}')
OPENAI_MODELS = (200, [], '{"data": []}')


def test_arty_engine_on_this_machine_is_found(local_ports):
    answers, _ = local_ports
    answers["localhost:11438/api/tags"] = ENGINE_TAGS
    assert asyncio.run(model_endpoints.detect_ollama(None)) == "http://localhost:11438"


def test_arty_engine_is_preferred_when_ollama_answers_too(local_ports):
    answers, _ = local_ports
    answers["127.0.0.1:11434/api/tags"] = OLLAMA_TAGS
    answers["127.0.0.1:11438/api/tags"] = ENGINE_TAGS
    assert asyncio.run(model_endpoints.detect_ollama(None)) == "http://127.0.0.1:11438"


@pytest.mark.parametrize("greeting", ["dead", (200, [], "Ollama is running"), (200, [], "something else")])
def test_a_server_on_the_engine_port_that_does_not_name_itself_is_passed_over(local_ports, greeting):
    answers, _ = local_ports
    answers["127.0.0.1:11438/api/tags"] = OLLAMA_TAGS
    answers["127.0.0.1:11438/"] = greeting
    answers["127.0.0.1:11434/api/tags"] = OLLAMA_TAGS
    assert asyncio.run(model_endpoints.detect_ollama(None)) == "http://127.0.0.1:11434"


def test_a_server_on_the_engine_port_that_does_not_name_itself_is_not_detected_alone(local_ports):
    answers, _ = local_ports
    answers["127.0.0.1:11438/api/tags"] = OLLAMA_TAGS
    assert asyncio.run(model_endpoints.detect_ollama(None)) is None


def test_arty_engine_is_named_from_its_server_header_even_behind_a_proxy(local_ports):
    answers, asked = local_ports
    answers["engine.test:11435/api/tags"] = ENGINE_BEHIND_CADDY
    assert asyncio.run(model_endpoints.identify_backend("http://engine.test:11435")) == "ArtyEngine"
    assert asked == ["engine.test:11435/api/tags"]


def test_arty_engine_is_named_from_its_greeting_when_a_proxy_drops_the_server_header(local_ports):
    answers, _ = local_ports
    answers["engine.test:11435/api/tags"] = OLLAMA_TAGS
    answers["engine.test:11435/"] = (200, [], "Arty engine is running")
    assert asyncio.run(model_endpoints.identify_backend("http://engine.test:11435/")) == "ArtyEngine"


@pytest.mark.parametrize("greeting", [(200, [], "Ollama is running"), "dead", 404])
def test_any_other_server_that_speaks_the_ollama_api_is_named_ollama(local_ports, greeting):
    answers, _ = local_ports
    answers["gpu-box:11434/api/tags"] = OLLAMA_TAGS
    answers["gpu-box:11434/"] = greeting
    assert asyncio.run(model_endpoints.identify_backend("http://gpu-box:11434/api")) == "Ollama"


@pytest.mark.parametrize("reply", ["dead", "hangs", 500])
def test_no_backend_is_named_when_nothing_healthy_answers(local_ports, reply):
    answers, _ = local_ports
    answers["gpu-box:11434"] = reply
    assert asyncio.run(model_endpoints.identify_backend("http://gpu-box:11434")) is None


def test_an_auto_detected_engine_is_added_once_with_its_name(local_ports, tmp_path):
    answers, _ = local_ports
    answers["127.0.0.1:11438/api/tags"] = ENGINE_BEHIND_CADDY
    store = ModelEndpointStore(str(tmp_path))
    added = asyncio.run(model_endpoints.add_detected_backend(store, "http://127.0.0.1:11438"))
    assert (added.base_url, added.kind, added.label) == ("http://127.0.0.1:11438", "ollama", "ArtyEngine (auto-detected)")
    assert asyncio.run(model_endpoints.add_detected_backend(store, "http://127.0.0.1:11438/")) is None
    assert len(store.list()) == 1


def test_an_auto_detected_address_that_does_not_answer_is_still_added_as_ollama(local_ports, tmp_path):
    store = ModelEndpointStore(str(tmp_path))
    added = asyncio.run(model_endpoints.add_detected_backend(store, "http://gpu-box:11434"))
    assert added.label == "Ollama (auto-detected)"


@pytest.mark.parametrize("stored_label, answer, relabelled", [
    ("Ollama (auto-detected)", ENGINE_TAGS, "ArtyEngine (auto-detected)"),
    ("ArtyEngine (auto-detected)", OLLAMA_TAGS, "Ollama (auto-detected)"),
    ("Ollama (auto-detected)", "dead", "Ollama (auto-detected)"),
    ("My engine", ENGINE_TAGS, "My engine"),
    ("My box", OLLAMA_TAGS, "My box"),
])
def test_a_stored_auto_detected_endpoint_is_renamed_after_the_backend_that_answers(
        local_ports, tmp_path, stored_label, answer, relabelled):
    answers, _ = local_ports
    answers["127.0.0.1:11438/api/tags"] = answer
    store = ModelEndpointStore(str(tmp_path))
    stored = store.add("http://127.0.0.1:11438", "ollama", label=stored_label)
    assert asyncio.run(model_endpoints.add_detected_backend(store, "http://127.0.0.1:11438")) is None
    [endpoint] = store.list()
    assert (endpoint.id, endpoint.label) == (stored.id, relabelled)


def never_start():
    async def start(_address=None):
        raise AssertionError("Ollama was started although a backend answered")
    return start


def test_startup_keeps_the_stored_backend_and_never_adds_a_second_local_one(local_ports, tmp_path):
    answers, asked = local_ports
    answers["127.0.0.1:11434/api/tags"] = OLLAMA_TAGS
    answers["127.0.0.1:11438/api/tags"] = ENGINE_TAGS
    answers["localhost:11438/api/tags"] = ENGINE_TAGS
    store = ModelEndpointStore(str(tmp_path))
    store.add("http://127.0.0.1:11434", "ollama", label="Ollama (auto-detected)")
    added = asyncio.run(model_endpoints.settle_local_backend(store, None, never_start()))
    assert added is None
    assert [(e.base_url, e.label) for e in store.list()] == [("http://127.0.0.1:11434", "Ollama (auto-detected)")]
    assert not [address for address in asked if ":11438" in address]


def test_startup_starts_ollama_when_the_stored_backend_is_down_and_still_adds_nothing_else(local_ports, tmp_path):
    answers, asked = local_ports
    answers["127.0.0.1:11438/api/tags"] = ENGINE_TAGS
    store = ModelEndpointStore(str(tmp_path))
    store.add("http://127.0.0.1:11434", "ollama", label="Ollama (auto-detected)")
    starts = []

    async def start(_address=None):
        starts.append(True)
        answers["127.0.0.1:11434/api/tags"] = OLLAMA_TAGS
        return True

    assert asyncio.run(model_endpoints.settle_local_backend(store, None, start)) is None
    assert starts == [True]
    assert [e.base_url for e in store.list()] == ["http://127.0.0.1:11434"]
    assert not [address for address in asked if ":11438" in address]


def test_startup_adds_the_backend_it_finds_when_none_is_stored(local_ports, tmp_path):
    answers, _ = local_ports
    answers["127.0.0.1:11438/api/tags"] = ENGINE_TAGS
    answers["127.0.0.1:11434/api/tags"] = OLLAMA_TAGS
    store = ModelEndpointStore(str(tmp_path))
    added = asyncio.run(model_endpoints.settle_local_backend(store, None, never_start()))
    assert (added.base_url, added.label) == ("http://127.0.0.1:11438", "ArtyEngine (auto-detected)")
    assert len(store.list()) == 1


def test_startup_adds_a_configured_address_without_probing_this_machine(local_ports, tmp_path):
    _, asked = local_ports
    store = ModelEndpointStore(str(tmp_path))
    store.add("http://127.0.0.1:11434", "ollama", label="Ollama (auto-detected)")
    added = asyncio.run(model_endpoints.settle_local_backend(store, "http://gpu-box:11434", never_start()))
    assert added.base_url == "http://gpu-box:11434"
    assert not [address for address in asked if address.startswith(("127.0.0.1", "localhost"))]


@pytest.mark.parametrize("stored, configured, chosen", [
    ([], None, None),
    ([("http://gpu-box:11434", "ollama")], None, None),
    ([("http://127.0.0.1:1234", "openai-compatible")], None, None),
    ([("http://gpu-box:11434", "ollama"), ("http://localhost:11438", "ollama"), ("http://127.0.0.1:11434", "ollama")],
     None, "http://localhost:11438"),
    ([("http://127.0.0.1:11434", "ollama")], "http://gpu-box:11434", "http://gpu-box:11434"),
])
def test_the_chosen_backend_is_the_configured_one_or_the_first_stored_on_this_machine(
        local_ports, tmp_path, stored, configured, chosen):
    _, asked = local_ports
    store = ModelEndpointStore(str(tmp_path))
    for base_url, kind in stored:
        store.add(base_url, kind)
    assert model_endpoints.chosen_backend_url(store, configured) == chosen
    assert asked == []


def test_discovery_lists_each_running_server_once_and_skips_dead_and_hanging_ports(local_ports):
    answers, _ = local_ports
    answers["127.0.0.1:11434/api/tags"] = OLLAMA_TAGS
    answers["localhost:11434/api/tags"] = OLLAMA_TAGS
    answers["127.0.0.1:1234/api/tags"] = 404
    answers["127.0.0.1:1234/v1/models"] = OPENAI_MODELS
    answers["127.0.0.1:8080"] = "hangs"
    found = asyncio.run(model_endpoints.discover_servers())
    assert found == [
        {"base_url": "http://127.0.0.1:11434", "kind": "ollama", "label": "Ollama (127.0.0.1:11434)"},
        {"base_url": "http://127.0.0.1:1234/v1", "kind": "openai-compatible", "label": "Local server (127.0.0.1:1234)"},
    ]


def test_discovery_finds_arty_engine_first_and_names_each_backend(local_ports):
    answers, _ = local_ports
    answers["127.0.0.1:11434/api/tags"] = OLLAMA_TAGS
    answers["127.0.0.1:11434/"] = (200, [], "Ollama is running")
    answers["127.0.0.1:11438/api/tags"] = (200, [("Server", "arty-engine/0.1.0 Python/3.13.5")], '{"models": []}')
    found = asyncio.run(model_endpoints.discover_servers())
    assert found == [
        {"base_url": "http://127.0.0.1:11438", "kind": "ollama", "label": "ArtyEngine (127.0.0.1:11438)"},
        {"base_url": "http://127.0.0.1:11434", "kind": "ollama", "label": "Ollama (127.0.0.1:11434)"},
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


@pytest.mark.parametrize("configured, stored, chosen", [
    (None, "http://localhost:11434/api", "http://localhost:11434"),
    (None, "http://127.0.0.1:11438/api/", "http://127.0.0.1:11438"),
    ("http://gpu-box:11434/api", None, "http://gpu-box:11434"),
    ("http://gpu-box:11434/api/chat", None, "http://gpu-box:11434"),
    ("http://gpu-box:11434", None, "http://gpu-box:11434"),
    (None, "http://localhost:11434/v1", "http://localhost:11434"),
    (None, "http://127.0.0.1:11434/v1/", "http://127.0.0.1:11434"),
    ("http://gpu-box:11434/v1", None, "http://gpu-box:11434"),
])
def test_the_chosen_backend_is_the_server_root_even_when_stored_with_an_api_path(
        local_ports, tmp_path, configured, stored, chosen):
    store = ModelEndpointStore(str(tmp_path))
    if stored:
        store.add(stored, "ollama")
    assert model_endpoints.chosen_backend_url(store, configured) == chosen


def test_startup_adopts_nothing_local_while_a_remote_backend_is_stored(local_ports, tmp_path):
    answers, asked = local_ports
    answers["127.0.0.1:11434/api/tags"] = OLLAMA_TAGS
    answers["127.0.0.1:11438/api/tags"] = OLLAMA_TAGS
    answers["127.0.0.1:11438/"] = (200, [], "Arty engine is running")
    store = ModelEndpointStore(str(tmp_path))
    store.add("http://gpu-box:11434", "ollama", label="GPU box")
    assert asyncio.run(model_endpoints.settle_local_backend(store, None, never_start())) is None
    assert [e.base_url for e in store.list()] == ["http://gpu-box:11434"]
    assert model_endpoints.chosen_backend_url(store, None) is None
    assert not [address for address in asked if address.startswith(("127.0.0.1", "localhost"))]


def test_a_scanned_server_without_the_ollama_api_is_refiled_and_does_not_block_detection(local_ports, tmp_path):
    answers, _ = local_ports
    answers["127.0.0.1:8080/api/tags"] = 404
    answers["127.0.0.1:8080/v1/models"] = OPENAI_MODELS
    answers["127.0.0.1:11434/api/tags"] = OLLAMA_TAGS
    store = ModelEndpointStore(str(tmp_path))
    store.add("http://127.0.0.1:8080", "ollama", label="Local server (127.0.0.1:8080)")
    added = asyncio.run(model_endpoints.settle_local_backend(store, None, never_start()))
    assert added.base_url == "http://127.0.0.1:11434"
    assert [(e.base_url, e.kind) for e in store.list()] == [("http://127.0.0.1:8080", "openai-compatible"),
                                                           ("http://127.0.0.1:11434", "ollama")]
    assert model_endpoints.chosen_backend_url(store, None) == "http://127.0.0.1:11434"


def test_a_stored_backend_that_is_only_down_keeps_its_kind(local_ports, tmp_path):
    store = ModelEndpointStore(str(tmp_path))
    store.add("http://127.0.0.1:11434", "ollama", label="Ollama (auto-detected)")

    async def start(_address=None):
        return False

    assert asyncio.run(model_endpoints.settle_local_backend(store, None, start)) is None
    assert [(e.base_url, e.kind) for e in store.list()] == [("http://127.0.0.1:11434", "ollama")]


@pytest.mark.parametrize("stored", ["http://[::1]:11434", "http://0.0.0.0:11434", "http://LOCALHOST:11434"])
def test_every_name_for_this_machine_counts_as_the_stored_local_backend(local_ports, tmp_path, stored):
    answers, _ = local_ports
    answers["127.0.0.1:11434/api/tags"] = OLLAMA_TAGS
    answers["::1:11434/api/tags"] = OLLAMA_TAGS
    answers["0.0.0.0:11434/api/tags"] = OLLAMA_TAGS
    answers["localhost:11434/api/tags"] = OLLAMA_TAGS
    store = ModelEndpointStore(str(tmp_path))
    store.add(stored, "ollama")
    assert asyncio.run(model_endpoints.settle_local_backend(store, None, never_start())) is None
    assert [e.base_url for e in store.list()] == [stored]
    assert model_endpoints.chosen_backend_url(store, None) == stored


@pytest.mark.parametrize("stored", ["http://[::1", "http://[localhost]:11434"])
def test_a_damaged_stored_address_is_not_a_local_backend_and_does_not_stop_startup(local_ports, tmp_path, stored):
    answers, _ = local_ports
    answers["127.0.0.1:11434/api/tags"] = OLLAMA_TAGS
    (tmp_path / "endpoints.json").write_text(json.dumps([{"id": "bad", "base_url": stored, "kind": "ollama"}]))
    store = ModelEndpointStore(str(tmp_path))
    assert model_endpoints.chosen_backend_url(store, None) is None
    added = asyncio.run(model_endpoints.settle_local_backend(store, None, never_start()))
    assert added is None
    assert [e.base_url for e in store.list()] == [stored]


def test_an_ollama_stored_with_its_openai_path_stays_the_chosen_backend(local_ports, tmp_path):
    answers, _ = local_ports
    answers["localhost:11434/api/tags"] = OLLAMA_TAGS
    store = ModelEndpointStore(str(tmp_path))
    store.add("http://localhost:11434/v1", "ollama")
    assert asyncio.run(model_endpoints.settle_local_backend(store, None, never_start())) is None
    assert [(e.base_url, e.kind) for e in store.list()] == [("http://localhost:11434/v1", "ollama")]
    assert model_endpoints.chosen_backend_url(store, None) == "http://localhost:11434"


def test_a_server_without_the_ollama_api_stored_with_its_openai_path_is_refiled(local_ports, tmp_path):
    answers, _ = local_ports
    answers["127.0.0.1:1234/api/tags"] = 404
    answers["127.0.0.1:1234/v1/models"] = OPENAI_MODELS
    answers["127.0.0.1:11434/api/tags"] = OLLAMA_TAGS
    store = ModelEndpointStore(str(tmp_path))
    store.add("http://127.0.0.1:1234/v1", "ollama")
    added = asyncio.run(model_endpoints.settle_local_backend(store, None, never_start()))
    assert added.base_url == "http://127.0.0.1:11434"
    assert [(e.base_url, e.kind) for e in store.list()] == [("http://127.0.0.1:1234/v1", "openai-compatible"),
                                                           ("http://127.0.0.1:11434", "ollama")]


@pytest.mark.parametrize("tags_reply", [500, 503, "hangs"])
def test_one_failed_answer_from_the_ollama_api_does_not_refile_the_server(local_ports, tmp_path, tags_reply):
    answers, _ = local_ports
    answers["127.0.0.1:11434/api/tags"] = tags_reply
    answers["127.0.0.1:11434/v1/models"] = OPENAI_MODELS
    store = ModelEndpointStore(str(tmp_path))
    store.add("http://127.0.0.1:11434", "ollama", label="Ollama (auto-detected)")

    async def start(_address=None):
        return False

    asyncio.run(model_endpoints.settle_local_backend(store, None, start))
    assert [(e.base_url, e.kind) for e in store.list()] == [("http://127.0.0.1:11434", "ollama")]


def test_a_refiled_server_that_speaks_the_ollama_api_again_is_filed_back_and_chosen(local_ports, tmp_path):
    answers, _ = local_ports
    answers["127.0.0.1:11434/api/tags"] = OLLAMA_TAGS
    store = ModelEndpointStore(str(tmp_path))
    store.add("http://127.0.0.1:11434", "openai-compatible", label="Ollama (auto-detected)")
    assert asyncio.run(model_endpoints.settle_local_backend(store, None, never_start())) is None
    assert [(e.base_url, e.kind) for e in store.list()] == [("http://127.0.0.1:11434", "ollama")]
    assert model_endpoints.chosen_backend_url(store, None) == "http://127.0.0.1:11434"


def test_a_server_that_answers_tags_without_ollama_models_is_not_filed_as_ollama(local_ports, tmp_path):
    answers, _ = local_ports
    answers["127.0.0.1:1234/api/tags"] = (200, [], "<html>not here</html>")
    store = ModelEndpointStore(str(tmp_path))
    store.add("http://127.0.0.1:1234/v1", "openai-compatible")

    async def start(_address=None):
        return False

    asyncio.run(model_endpoints.settle_local_backend(store, None, start))
    assert [(e.base_url, e.kind) for e in store.list()] == [("http://127.0.0.1:1234/v1", "openai-compatible")]


def test_starting_ollama_files_back_a_refiled_address_and_chooses_it(local_ports, tmp_path):
    answers, _ = local_ports
    answers["127.0.0.1:11434/api/tags"] = OLLAMA_TAGS
    store = ModelEndpointStore(str(tmp_path))
    store.add("http://127.0.0.1:11434", "openai-compatible", label="Ollama (auto-detected)")
    asyncio.run(model_endpoints.adopt_started_ollama(store, None))
    assert [(e.base_url, e.kind) for e in store.list()] == [("http://127.0.0.1:11434", "ollama")]
    assert model_endpoints.chosen_backend_url(store, None) == "http://127.0.0.1:11434"


@pytest.mark.parametrize("stored", ["http://gpu-box:11434", "http://gpu-box:11434/api", "http://10.0.0.5:11434"])
def test_an_ollama_server_added_with_a_key_also_stops_adoption(local_ports, tmp_path, stored):
    answers, asked = local_ports
    answers["127.0.0.1:11434/api/tags"] = OLLAMA_TAGS
    store = ModelEndpointStore(str(tmp_path))
    store.add(stored, "api-key", label="GPU box")
    assert store.list()[0].provider == "ollama"
    assert asyncio.run(model_endpoints.settle_local_backend(store, None, never_start())) is None
    assert [e.base_url for e in store.list()] == [stored]
    assert not [address for address in asked if address.startswith(("127.0.0.1", "localhost"))]


def test_an_ollama_cloud_key_does_not_stop_adoption_of_the_ollama_on_this_machine(local_ports, tmp_path):
    answers, _ = local_ports
    answers["127.0.0.1:11434/api/tags"] = OLLAMA_TAGS
    store = ModelEndpointStore(str(tmp_path))
    store.add("https://ollama.com", "api-key", label="Ollama Cloud")
    added = asyncio.run(model_endpoints.settle_local_backend(store, None, never_start()))
    assert added.base_url == "http://127.0.0.1:11434"



def test_a_server_that_answers_200_on_every_path_is_not_taken_for_ollama(local_ports):
    answers, _ = local_ports
    answers["127.0.0.1:1234"] = ANSWERS_EVERY_PATH
    answers["127.0.0.1:1234/v1/models"] = OPENAI_MODELS
    assert asyncio.run(model_endpoints.identify_backend("http://127.0.0.1:1234")) is None
    assert asyncio.run(model_endpoints.discover_servers()) == [
        {"base_url": "http://127.0.0.1:1234/v1", "kind": "openai-compatible", "label": "Local server (127.0.0.1:1234)"},
    ]


def test_a_local_server_that_answers_200_on_every_path_is_filed_by_its_openai_model_list(local_ports):
    answers, _ = local_ports
    answers["localhost:1234"] = ANSWERS_EVERY_PATH
    answers["localhost:1234/v1/models"] = OPENAI_MODELS
    filing = asyncio.run(model_endpoints.filing_for_local_server("http://localhost:1234/v1"))
    assert filing == ("http://localhost:1234/v1", "openai-compatible")


def test_a_local_server_that_answers_200_on_every_path_without_any_model_list_is_not_filed(local_ports):
    answers, _ = local_ports
    answers["localhost:1234"] = ANSWERS_EVERY_PATH
    assert asyncio.run(model_endpoints.filing_for_local_server("http://localhost:1234/v1")) is None


def test_a_stored_ollama_that_answers_200_on_every_path_is_refiled_as_openai_compatible(local_ports, tmp_path):
    answers, _ = local_ports
    answers["127.0.0.1:1234"] = ANSWERS_EVERY_PATH
    answers["127.0.0.1:1234/v1/models"] = OPENAI_MODELS
    store = ModelEndpointStore(str(tmp_path))
    store.add("http://127.0.0.1:1234/v1", "ollama")

    async def start(_address=None):
        return False

    asyncio.run(model_endpoints.settle_local_backend(store, None, start))
    assert [(e.base_url, e.kind) for e in store.list()] == [("http://127.0.0.1:1234/v1", "openai-compatible")]
    assert model_endpoints.chosen_backend_url(store, None) is None


@pytest.mark.parametrize("stored, chosen", [("http://localhost:11434", "http://localhost:11434"),
                                            ("http://127.0.0.1:11434", "http://127.0.0.1:11434"),
                                            ("http://127.0.0.1:11434/api", "http://127.0.0.1:11434")])
def test_an_ollama_on_this_machine_added_with_a_key_is_still_used_for_embeddings(local_ports, tmp_path, stored, chosen):
    answers, _ = local_ports
    answers["127.0.0.1:11434/api/tags"] = OLLAMA_TAGS
    answers["localhost:11434/api/tags"] = OLLAMA_TAGS
    store = ModelEndpointStore(str(tmp_path))
    store.add(stored, "api-key", label="My Ollama")
    assert store.list()[0].provider == "ollama"
    asyncio.run(model_endpoints.settle_local_backend(store, None, never_start()))
    assert [(e.base_url, e.kind, e.label) for e in store.list()] == [(stored, "ollama", "My Ollama")]
    assert model_endpoints.chosen_backend_url(store, None) == chosen


def test_an_ollama_on_this_machine_on_another_port_added_with_a_key_is_used_without_starting_ollama(local_ports, tmp_path):
    answers, _ = local_ports
    answers["localhost:11500/api/tags"] = OLLAMA_TAGS
    store = ModelEndpointStore(str(tmp_path))
    store.add("http://localhost:11500", "api-key", label="My other Ollama")
    assert asyncio.run(model_endpoints.settle_local_backend(store, None, never_start())) is None
    assert [(e.base_url, e.kind) for e in store.list()] == [("http://localhost:11500", "ollama")]
    assert model_endpoints.chosen_backend_url(store, None) == "http://localhost:11500"


@pytest.mark.parametrize("stored", ["http://localhost:11434", "http://127.0.0.1:11434/api"])
def test_an_ollama_added_with_a_key_that_starts_late_is_filed_in_place_and_not_added_twice(local_ports, tmp_path, stored):
    answers, _ = local_ports
    store = ModelEndpointStore(str(tmp_path))
    store.add(stored, "api-key", label="My Ollama")

    async def start(_address=None):
        answers["127.0.0.1:11434/api/tags"] = OLLAMA_TAGS
        answers["localhost:11434/api/tags"] = OLLAMA_TAGS
        return True

    assert asyncio.run(model_endpoints.settle_local_backend(store, None, start)) is None
    assert [(e.base_url, e.kind) for e in store.list()] == [(stored, "ollama")]


def test_a_configured_address_for_a_stored_local_server_spelt_another_way_is_not_added_again(local_ports, tmp_path):
    answers, _ = local_ports
    answers["127.0.0.1:11434/api/tags"] = OLLAMA_TAGS
    store = ModelEndpointStore(str(tmp_path))
    store.add("http://localhost:11434", "ollama", label="Ollama (auto-detected)")
    assert asyncio.run(model_endpoints.settle_local_backend(store, "http://127.0.0.1:11434", never_start())) is None
    assert [e.base_url for e in store.list()] == ["http://localhost:11434"]


NO_OLLAMA_MODEL_LISTS = [404, ANSWERS_EVERY_PATH]


@pytest.mark.parametrize("port", [11500, 11434])
@pytest.mark.parametrize("tags_reply", NO_OLLAMA_MODEL_LISTS)
def test_an_ollama_added_with_its_openai_path_is_filed_back_after_one_start_without_a_model_list(
        local_ports, tmp_path, port, tags_reply):
    answers, _ = local_ports
    answers[f"127.0.0.1:{port}/api/tags"] = tags_reply
    answers[f"127.0.0.1:{port}/v1/models"] = OPENAI_MODELS
    store = ModelEndpointStore(str(tmp_path))
    store.add(f"http://127.0.0.1:{port}/v1", "ollama")

    async def start(_address=None):
        return False

    asyncio.run(model_endpoints.settle_local_backend(store, None, start))
    assert [e.kind for e in store.list()] == ["openai-compatible"]
    answers[f"127.0.0.1:{port}/api/tags"] = OLLAMA_TAGS
    asyncio.run(model_endpoints.settle_local_backend(store, None, never_start()))
    asyncio.run(model_endpoints.settle_local_backend(store, None, never_start()))
    assert [(e.base_url, e.kind) for e in store.list()] == [(f"http://127.0.0.1:{port}/v1", "ollama")]
    assert model_endpoints.chosen_backend_url(store, None) == f"http://127.0.0.1:{port}"


def test_an_ollama_stored_before_endpoints_remembered_how_they_were_added_can_be_filed_back(local_ports, tmp_path):
    answers, _ = local_ports
    answers["127.0.0.1:11500/api/tags"] = 404
    answers["127.0.0.1:11500/v1/models"] = OPENAI_MODELS
    store = write_endpoints(tmp_path, [{"id": "old", "base_url": "http://127.0.0.1:11500/v1", "kind": "ollama"}])

    async def start(_address=None):
        return False

    asyncio.run(model_endpoints.settle_local_backend(store, None, start))
    answers["127.0.0.1:11500/api/tags"] = OLLAMA_TAGS
    asyncio.run(model_endpoints.settle_local_backend(store, None, never_start()))
    assert [(e.id, e.kind) for e in store.list()] == [("old", "ollama")]


def test_an_openai_server_stored_before_endpoints_remembered_how_they_were_added_stays_openai(local_ports, tmp_path):
    answers, _ = local_ports
    answers["127.0.0.1:8080/api/tags"] = OLLAMA_TAGS
    answers["127.0.0.1:8080/v1/models"] = OPENAI_MODELS
    store = write_endpoints(tmp_path, [{"id": "old", "base_url": "http://127.0.0.1:8080/v1", "kind": "openai-compatible"}])

    async def start(_address=None):
        return False

    asyncio.run(model_endpoints.settle_local_backend(store, None, start))
    assert [(e.id, e.kind) for e in store.list()] == [("old", "openai-compatible")]


def test_the_log_names_the_server_that_stops_detection_and_never_calls_this_machine_another(
        local_ports, tmp_path, caplog):
    answers, _ = local_ports
    answers["127.0.0.1:11434/api/tags"] = OLLAMA_TAGS
    store = ModelEndpointStore(str(tmp_path))
    store.add("http://gpu-box:11434", "api-key", label="GPU box")
    with caplog.at_level("INFO", logger=model_endpoints.logger.name):
        asyncio.run(model_endpoints.settle_local_backend(store, None, never_start()))
    lines = [record.getMessage() for record in caplog.records]
    assert any("http://gpu-box:11434" in line for line in lines), lines
    assert not any("another machine" in line for line in lines), lines


def test_an_openai_server_that_starts_listing_ollama_tags_does_not_become_the_embeddings_server(local_ports, tmp_path):
    answers, _ = local_ports
    answers["127.0.0.1:8080/api/tags"] = OLLAMA_TAGS
    answers["127.0.0.1:8080/v1/models"] = OPENAI_MODELS
    store = ModelEndpointStore(str(tmp_path))
    store.add("http://127.0.0.1:8080/v1", "openai-compatible", label="Local server (127.0.0.1:8080)")

    async def start(_address=None):
        return False

    asyncio.run(model_endpoints.settle_local_backend(store, None, start))
    assert [(e.base_url, e.kind) for e in store.list()] == [("http://127.0.0.1:8080/v1", "openai-compatible")]
    assert model_endpoints.chosen_backend_url(store, None) is None


def test_an_openai_server_named_by_a_configured_address_is_not_filed_as_ollama(local_ports, tmp_path):
    answers, _ = local_ports
    answers["127.0.0.1:8080/v1/tags"] = OLLAMA_TAGS
    store = ModelEndpointStore(str(tmp_path))
    store.add("http://127.0.0.1:8080/v1", "openai-compatible")
    asyncio.run(model_endpoints.add_detected_backend(store, "http://127.0.0.1:8080/v1"))
    assert [(e.base_url, e.kind) for e in store.list()] == [("http://127.0.0.1:8080/v1", "openai-compatible")]
