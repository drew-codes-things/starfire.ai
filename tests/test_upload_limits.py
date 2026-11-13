import io
import os
import sys
import zipfile

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import auth
import auth_routes
import documents_store
import routes
import upload_limits
import user_data
from auth import SessionStore, UserStore
from config import config

PASSWORD = "correct horse battery staple"
HEADERS = {"X-Starfire-Client": "1"}
MIB = 1024 * 1024
PNG = b"\x89PNG" + b"x" * (700 * 1024)


@pytest.fixture
def admin(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    users, sessions = UserStore(data_dir), SessionStore(data_dir)
    monkeypatch.setattr(config, "data_dir", data_dir)
    monkeypatch.setattr(auth_routes, "users", users)
    monkeypatch.setattr(auth_routes, "sessions", sessions)
    monkeypatch.setattr(auth_routes, "known_addresses", auth.KnownAddresses(data_dir))
    monkeypatch.setattr(routes, "registry", user_data.configure(data_dir))
    users.upsert("admin@example.com", "Admin", "admin", bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode())
    app = FastAPI()
    upload_limits.install(app, config)
    auth.install(app, users, sessions)
    app.include_router(auth_routes.router)
    app.include_router(routes.router)
    client = TestClient(app, base_url="https://testserver", headers=HEADERS)
    assert client.post("/api/auth/login", json={"email": "admin@example.com", "password": PASSWORD}).status_code == 200
    return client


def disk_nearly_full(monkeypatch):
    monkeypatch.setattr(config, "min_free_disk_mb", 10 ** 9)


def test_a_document_over_the_size_limit_is_refused_and_not_stored(admin, monkeypatch):
    monkeypatch.setattr(config, "max_document_mb", 1)
    response = admin.post("/api/documents", files={"file": ("big.txt", b"word. " * (MIB // 3), "text/plain")})
    assert response.status_code == 413
    assert admin.get("/api/documents").json()["documents"] == []


def test_a_document_is_refused_when_the_disk_is_nearly_full(admin, monkeypatch):
    disk_nearly_full(monkeypatch)
    response = admin.post("/api/documents", files={"file": ("small.txt", b"One line. Two lines.", "text/plain")})
    assert response.status_code == 507
    assert admin.get("/api/documents").json()["documents"] == []


def test_a_small_document_is_still_accepted(admin):
    response = admin.post("/api/documents", files={"file": ("small.txt", b"One line. Two lines.", "text/plain")})
    assert response.status_code == 200 and response.json()["chunk_count"] == 1


def test_text_without_sentence_breaks_is_cut_into_bounded_chunks():
    pieces = documents_store.split_into_chunks("word " * 5000)
    assert len(pieces) > 1
    assert max(len(piece) for piece in pieces) <= documents_store.CHUNK_SIZE + documents_store.CHUNK_OVERLAP + 1


def test_attachments_stop_at_the_per_account_total(admin, monkeypatch):
    monkeypatch.setattr(config, "max_attachments_mb", 1)
    first = admin.post("/api/attachments", files={"file": ("a.png", PNG, "image/png")})
    assert first.status_code == 200
    second = admin.post("/api/attachments", files={"file": ("b.png", PNG, "image/png")})
    assert second.status_code == 413
    assert "1 MB" in second.json()["detail"]


def test_attachments_are_refused_when_the_disk_is_nearly_full(admin, monkeypatch):
    disk_nearly_full(monkeypatch)
    assert admin.post("/api/attachments", files={"file": ("a.png", PNG, "image/png")}).status_code == 507


def test_an_upload_body_over_its_cap_is_refused_before_it_is_read(admin, monkeypatch):
    monkeypatch.setattr(config, "max_document_mb", 1)
    response = admin.post("/api/documents", content=b"x" * (3 * MIB),
                          headers={"Content-Type": "multipart/form-data; boundary=zzz"})
    assert response.status_code == 413


def test_a_chunked_upload_body_is_cut_off_at_its_cap(admin, monkeypatch):
    monkeypatch.setattr(config, "max_document_mb", 1)

    def body():
        yield b"--zzz\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.txt\"\r\n\r\n"
        for _ in range(3):
            yield b"x" * MIB

    response = admin.post("/api/documents", content=body(), headers={"Content-Type": "multipart/form-data; boundary=zzz"})
    assert response.status_code == 413


def zip_of(files: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_a_restore_over_the_size_limit_is_refused(admin, monkeypatch):
    monkeypatch.setattr(config, "max_restore_mb", 1)
    archive = zip_of({"noise.bin": os.urandom(2 * MIB)})
    response = admin.post("/api/restore", files={"file": ("backup.zip", archive, "application/zip")})
    assert response.status_code == 413


def test_a_restore_that_would_fill_the_disk_is_refused_and_nothing_is_written(admin, monkeypatch, tmp_path):
    archive = zip_of({"restored.txt": b"0" * MIB})
    monkeypatch.setattr(upload_limits, "room_on_disk", lambda path, reserve_mb: 100 * 1024)
    assert len(archive) < 100 * 1024
    response = admin.post("/api/restore", files={"file": ("backup.zip", archive, "application/zip")})
    assert response.status_code == 507
    assert not (tmp_path / "restored.txt").exists()


def test_a_restore_that_fits_is_written(admin, tmp_path):
    archive = zip_of({"restored.txt": b"hello"})
    assert admin.post("/api/restore", files={"file": ("backup.zip", archive, "application/zip")}).status_code == 200
    assert (tmp_path / "restored.txt").read_bytes() == b"hello"


def test_a_backup_is_a_zip_of_the_data_folder(admin, tmp_path):
    (tmp_path / "marker.txt").write_text("kept")
    response = admin.get("/api/backup")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("marker.txt") == b"kept"


def test_a_backup_is_refused_when_the_disk_cannot_hold_it(admin, monkeypatch):
    disk_nearly_full(monkeypatch)
    assert admin.get("/api/backup").status_code == 507
