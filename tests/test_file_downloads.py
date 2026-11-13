import json
import os
import sys
from types import SimpleNamespace

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import auth
import auth_routes
import builtin_tools
import routes
import user_data
from auth import SessionStore, UserStore
from local_gen_store import GeneratedFileStore

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}
PNG = b"\x89PNG fake image bytes"


@pytest.fixture
def admin(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    users, sessions = UserStore(data_dir), SessionStore(data_dir)
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(auth_routes, "known_addresses", auth.KnownAddresses(data_dir))
    monkeypatch.setattr(routes, "registry", user_data.configure(data_dir))
    user = users.upsert("admin@example.com", "Admin", "admin",
                        bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode())
    app = FastAPI()
    auth.install(app, users, sessions)
    app.include_router(auth_routes.router)
    app.include_router(routes.router)
    client = TestClient(app, base_url="https://testserver", headers=HEADERS)
    assert client.post("/api/auth/login", json={"email": "admin@example.com", "password": PASSWORD}).status_code == 200
    client.user_id = user.id
    return client


def generated(admin, filename, content_type="text/plain", data=b"hello"):
    with user_data.run_as(admin.user_id):
        return routes.generated_files.add("document", filename, content_type, data)


def test_a_generated_file_with_a_non_latin_name_downloads(admin):
    entry = generated(admin, "報告 – draft.txt")
    response = admin.get(f"/api/generated/{entry.id}")
    assert response.status_code == 200 and response.content == b"hello"
    assert "filename*=utf-8''%E5%A0%B1%E5%91%8A" in response.headers["content-disposition"]


def test_a_quote_in_a_generated_name_cannot_add_a_second_filename(admin):
    entry = generated(admin, 'a"; filename="b.html.txt')
    disposition = admin.get(f"/api/generated/{entry.id}").headers["content-disposition"]
    assert 'filename="b.html.txt"' not in disposition
    assert disposition.startswith("inline")


def test_generated_document_names_lose_quotes_slashes_and_control_characters(tmp_path):
    ctx = SimpleNamespace(generated_files=GeneratedFileStore(str(tmp_path)))
    result = json.loads(builtin_tools._generate_document(
        {"content": "hi", "format": "txt", "filename": 'a"/b\\c\x07d'}, ctx))
    assert result["filename"] == "a__b_c_d.txt"


def test_generated_files_and_attachments_cannot_run_scripts_in_this_site(admin):
    entry = generated(admin, "page.txt", "text/html", b"<script>alert(1)</script>")
    attachment = admin.post("/api/attachments", files={"file": ("cat.png", PNG, "image/png")}).json()
    for path in (f"/api/generated/{entry.id}", f"/api/attachments/{attachment['id']}"):
        response = admin.get(path)
        assert response.headers["x-content-type-options"] == "nosniff", path
        assert response.headers["content-security-policy"] == "sandbox", path


@pytest.mark.parametrize("mime", ["image/svg+xml", "image/x-icon", "image/bmp"])
def test_only_plain_raster_images_can_be_attached(admin, mime):
    response = admin.post("/api/attachments", files={"file": ("x.img", b"<svg/>", mime)})
    assert response.status_code == 400


@pytest.mark.parametrize("mime", ["image/png", "image/jpeg", "image/gif", "image/webp"])
def test_common_images_can_still_be_attached(admin, mime):
    assert admin.post("/api/attachments", files={"file": ("x.img", PNG, mime)}).status_code == 200


def test_generated_pdfs_keep_working_in_the_browser_pdf_viewer(admin):
    entry = generated(admin, "report.pdf", "application/pdf", b"%PDF-1.4")
    response = admin.get(f"/api/generated/{entry.id}")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" not in response.headers
