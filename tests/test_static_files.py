import gzip
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import static_files

LARGE_SCRIPT = "const starfire = 'local-first';\n" * 400
SMALL_SCRIPT = "const tiny = 1;\n"


@pytest.fixture
def client(tmp_path):
    (tmp_path / "app.js").write_text(LARGE_SCRIPT, encoding="utf-8")
    (tmp_path / "tiny.js").write_text(SMALL_SCRIPT, encoding="utf-8")
    (tmp_path / "index.html").write_text("<!DOCTYPE html><title>starfire.ai</title>", encoding="utf-8")
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"ok": True, "padding": "x" * 4000}

    app.mount("/", static_files.compressed_static(str(tmp_path)), name="static")
    return TestClient(app)


def test_large_static_files_are_gzipped_when_the_browser_accepts_it(client):
    response = client.get("/app.js", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert "accept-encoding" in response.headers["vary"].lower()
    assert response.text == LARGE_SCRIPT
    assert int(response.headers["content-length"]) < len(LARGE_SCRIPT) / 4


def test_the_compressed_body_is_real_gzip(client):
    with client.stream("GET", "/app.js", headers={"Accept-Encoding": "gzip"}) as response:
        raw = b"".join(response.iter_raw())
    assert gzip.decompress(raw).decode() == LARGE_SCRIPT


def test_static_files_are_plain_without_accept_encoding(client):
    response = client.get("/app.js", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in response.headers
    assert response.text == LARGE_SCRIPT


def test_small_files_are_left_alone(client):
    response = client.get("/tiny.js", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in response.headers
    assert response.text == SMALL_SCRIPT


def test_api_responses_are_never_compressed_by_the_static_wrapper(client):
    response = client.get("/api/health", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in response.headers


def test_the_index_page_is_still_served_at_the_root(client):
    assert "starfire.ai" in client.get("/").text
