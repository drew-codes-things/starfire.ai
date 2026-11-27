import os
import sys

import bcrypt
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import auth
import auth_routes
import chat_stream
import email_client
import model_access
import routes
import user_data
from api_key_manager import APIKeyManager
from auth import SessionStore, UserStore
from config import config
from generation_quota import GenerationQuota
from model_access import ModelAccessStore
from model_endpoints import ModelEndpointStore

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}
PATTERNS = "*-abl,*-abl-*,*abliterated*,example-uncensored,own/private-model"
SPELLINGS = [
    "example-abl:latest", "library/example-abl:latest", "EXAMPLE-ABL", "example-abl ", " Example-Abl:Q4_K_M",
    "registry.example:5000/library/example-abl:q4", "example-abl@sha256:0123abcd", "example-abliterated:14b",
    "Example-Uncensored:latest", "own/private-model:latest", "OWN/Private-Model",
    "library/Example-Uncensored:latest", "registry.example:5000/example-uncensored",
]
ALLOWED = ["gpt-4o", "llama3.1:8b", "example-able", "registry.example:5000/llama3"]


@pytest.mark.parametrize("model", SPELLINGS)
def test_every_spelling_of_a_restricted_model_matches(model):
    assert model_access.matches_any(model, PATTERNS.split(","))


@pytest.mark.parametrize("model", ALLOWED)
def test_ordinary_models_do_not_match(model):
    assert not model_access.matches_any(model, PATTERNS.split(","))


def test_an_admins_allow_list_accepts_a_tag_but_not_another_namespace():
    assert model_access.may_use("llama3:latest", False, ["llama3"], [])
    assert model_access.may_use("LLAMA3 ", False, ["llama3"], [])
    assert not model_access.may_use("someone/llama3", False, ["llama3"], [])
    assert model_access.may_use("example-abl:latest", True, None, ["*-abl"])


NAMED_RESTRICTED = ["*-abl", "*-abl-*", "*abliterated*", "*private-model*"]


def test_an_allow_list_entry_without_a_tag_does_not_admit_restricted_variants():
    assert model_access.may_use("qwen3:14b", False, ["qwen3"], NAMED_RESTRICTED)
    assert not model_access.may_use("qwen3:14b-abliterated", False, ["qwen3"], NAMED_RESTRICTED)
    assert not model_access.may_use("qwen3:evil-abl", False, ["qwen3"], NAMED_RESTRICTED)


@pytest.mark.parametrize("allowed", [["*"], ["own/*"], ["*private-model*"], ["OWN/*"]])
def test_restricted_patterns_win_over_allow_list_wildcards(allowed):
    assert not model_access.may_use("own/Private-Model", False, allowed, NAMED_RESTRICTED)
    assert not model_access.may_use("own/Private-Model:latest", False, allowed, NAMED_RESTRICTED)


def test_only_an_exact_allow_list_entry_overrides_the_restricted_patterns():
    assert model_access.may_use("own/Persona", False, ["own/*"], NAMED_RESTRICTED)
    assert model_access.may_use("own/Private-Model", False, ["own/private-model"], NAMED_RESTRICTED)
    assert model_access.may_use("own/Private-Model:latest", False, ["own/Private-Model"], NAMED_RESTRICTED)
    assert model_access.may_use("example-abl", False, ["example-abl:latest"], NAMED_RESTRICTED)
    assert model_access.may_use("example-abl:q4", False, ["example-abl:q4"], NAMED_RESTRICTED)
    assert not model_access.may_use("example-abl:q4", False, ["example-abl"], NAMED_RESTRICTED)
    assert not model_access.may_use("example-abl:q4", False, ["example-abl:q*"], NAMED_RESTRICTED)
    assert not model_access.may_use("own/*private-model*", False, ["own/*private-model*"], NAMED_RESTRICTED)


@pytest.fixture
def app(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    users, sessions = UserStore(data_dir), SessionStore(data_dir)
    monkeypatch.setattr(config, "data_dir", data_dir)
    monkeypatch.setattr(config, "ollama_base_url", None)
    monkeypatch.setattr(config, "restricted_models", PATTERNS)
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(auth_routes, "known_addresses", auth.KnownAddresses(data_dir))
    monkeypatch.setattr(routes, "registry", user_data.configure(data_dir))
    monkeypatch.setattr(routes, "api_keys", APIKeyManager(data_dir))
    monkeypatch.setattr(routes, "endpoints", ModelEndpointStore(data_dir))
    monkeypatch.setattr(routes, "model_access_store", ModelAccessStore(data_dir))
    monkeypatch.setattr(routes, "generation_quota", GenerationQuota())
    hashed = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    users.upsert("admin@example.com", "Admin", "admin", hashed)
    users.upsert("sam@example.com", "Sam", "user", hashed)
    application = FastAPI()
    auth.install(application, users, sessions)
    application.include_router(auth_routes.router)
    application.include_router(routes.router)
    return application


@pytest.fixture
def endpoint(app):
    return routes.endpoints.add("https://models.example.com/v1", kind="api-key")


def login(app, email):
    client = TestClient(app, base_url="https://testserver", headers=HEADERS)
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    client.user_id = auth_routes.users.find_by_email(email).id
    return client


@pytest.fixture
def replies(monkeypatch):
    asked = []

    async def fake_stream(base_url, api_key, model, messages, system, options):
        asked.append(model)
        yield chat_stream._sse_line({"delta": "hi"})
        yield chat_stream._sse_line({"done": True})

    monkeypatch.setattr(routes, "stream_chat", fake_stream)
    return asked


def test_the_model_list_hides_every_spelling_from_an_ordinary_account(app, endpoint, monkeypatch):
    real_client = httpx.AsyncClient

    def answer(request):
        return httpx.Response(200, json={"data": [{"id": name} for name in SPELLINGS + ALLOWED]})

    monkeypatch.setattr(routes.httpx, "AsyncClient",
                        lambda *args, **kwargs: real_client(*args, transport=httpx.MockTransport(answer), **kwargs))
    sam, admin = login(app, "sam@example.com"), login(app, "admin@example.com")
    assert [m["id"] for m in sam.get("/api/models").json()["models"]] == ALLOWED
    assert len(admin.get("/api/models").json()["models"]) == len(SPELLINGS + ALLOWED)


@pytest.mark.parametrize("model", SPELLINGS)
def test_every_route_that_takes_a_model_refuses_every_spelling(app, endpoint, replies, monkeypatch, model):
    sam = login(app, "sam@example.com")
    warmed = []
    monkeypatch.setattr(routes, "_warm_brain_model", lambda *args: warmed.append(args))
    answers = {
        "chat_stream": sam.post("/api/chat_stream", json={"endpoint_id": endpoint.id, "model": model,
                                                          "messages": [{"role": "user", "content": "hi"}]}),
        "quick-complete": sam.post("/api/quick-complete", json={"endpoint_id": endpoint.id, "model": model,
                                                                "prompt": "x"}),
        "tasks": sam.post("/api/tasks", json={"prompt": "x", "schedule": "daily", "scheduled_time": "08:00",
                                              "endpoint_id": endpoint.id, "model": model}),
        "email rules": sam.post("/api/email/rules", json={"account_id": "a", "match_value": "x",
                                                          "action": "ai_summarize_note",
                                                          "endpoint_id": endpoint.id, "model": model}),
        "speak": sam.post("/api/speak", json={"model": model, "text": "hi"}),
    }
    assert {route: answer.status_code for route, answer in answers.items()} == dict.fromkeys(answers, 403)
    assert sam.post("/api/warm", json={"endpoint_id": endpoint.id, "model": model}).json() == {"ok": True}
    assert replies == [] and warmed == [] and sam.get("/api/tasks").json()["tasks"] == []


@pytest.fixture
def sams_mailbox(app, monkeypatch):
    sam = login(app, "sam@example.com")
    with user_data.run_as(sam.user_id):
        account = routes.email_accounts.add(label="Home", email_address="sam@home.example",
                                            imap_host="imap.home.example", imap_port=993,
                                            smtp_host="smtp.home.example", smtp_port=587, username="")
    routes.api_keys.save(account.id, "pw")
    opened = []

    def read_message(*args, **kwargs):
        opened.append(args)
        return {"uid": "1", "from": "a@b.example", "to": "", "subject": "s", "date": "", "message_id": "",
                "body": "hello"}

    monkeypatch.setattr(email_client, "read_message", read_message)
    return sam, account.id, opened


def ai_action(sam, account_id, endpoint_id, model):
    return sam.post(f"/api/email/{account_id}/message/1/ai?action=summarize",
                    json={"endpoint_id": endpoint_id, "model": model})


@pytest.mark.parametrize("model", ["example-abl", "library/example-abl:latest"])
def test_the_email_ai_action_refuses_a_restricted_model_before_reading_mail(app, endpoint, sams_mailbox, model):
    sam, account_id, opened = sams_mailbox
    refused = ai_action(sam, account_id, endpoint.id, model)
    assert refused.status_code == 403 and opened == []


def test_the_email_ai_action_counts_toward_the_reply_quota(app, endpoint, sams_mailbox, monkeypatch):
    sam, account_id, opened = sams_mailbox
    monkeypatch.setattr(config, "generations_per_minute", 1)

    async def answer(action, message, instruction, ctx):
        return "summary"

    monkeypatch.setattr(routes.builtin_tools, "email_ai_action", answer)
    assert ai_action(sam, account_id, endpoint.id, "gpt-4o").status_code == 200
    limited = ai_action(sam, account_id, endpoint.id, "gpt-4o")
    assert limited.status_code == 429 and len(opened) == 1


RESTRICTED_FILE_NAMES = ["qwen3-8b-abl.i1-Q4_K_M.gguf", "x-abl.Q4-K-M.gguf", "x-abl.bf16-00001-of-00003.gguf",
                         "x-abl.Q4_K_M-imat.gguf", "x-abl (1).gguf", "x-abl/", "x-abl\\", " x-abl (2) ",
                         "E:\\models\\x-abl (2).gguf", "/srv/models/x-abl.i1-IQ3_XS.gguf/"]
ORDINARY_FILE_NAMES = ["llama3.1:8b", "qwen2.5-coder:14b", "mixtral-8x7b.Q4_K_M.gguf", "phi-3.5-mini (1).gguf",
                       "example-able.Q4-K-M.gguf"]


@pytest.mark.parametrize("model", RESTRICTED_FILE_NAMES)
def test_quantised_split_copied_and_slashed_names_of_a_restricted_model_match(model):
    assert model_access.matches_any(model, type(config)().restricted_model_patterns())


@pytest.mark.parametrize("model", ORDINARY_FILE_NAMES)
def test_dotted_file_names_of_ordinary_models_stay_ordinary(model):
    assert not model_access.matches_any(model, type(config)().restricted_model_patterns())


PLAIN_PATTERNS_AND_DOTTED_VERSIONS = [("qwen2", "qwen2.5-coder:14b"), ("gpt-4", "gpt-4.1-mini"),
                                      ("llama3", "llama3.2-vision:11b"), ("phi3", "phi3.5:latest"),
                                      ("*-v1", "model-v1.5:latest"), ("dolphin3", "dolphin3.0-llama3.1:8b")]


@pytest.mark.parametrize("pattern, model", PLAIN_PATTERNS_AND_DOTTED_VERSIONS)
def test_a_version_number_with_a_dot_is_never_cut_into_an_older_name(pattern, model):
    assert not model_access.matches_any(model, [pattern])
