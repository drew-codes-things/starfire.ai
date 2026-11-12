import os
import stat
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import pytest

import atomic_io
from api_key_manager import APIKeyManager

PRIVATE = 0o600


@pytest.fixture
def open_umask():
    previous = os.umask(0o022)
    yield
    os.umask(previous)


def mode_of(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_a_written_store_is_private_from_the_first_byte(tmp_path, monkeypatch, open_umask):
    target = tmp_path / "users.json"
    modes_before_replace = []
    real_replace = os.replace

    def checking_replace(source, destination):
        modes_before_replace.append(mode_of(source))
        real_replace(source, destination)

    monkeypatch.setattr(atomic_io.os, "replace", checking_replace)
    atomic_io.atomic_write_json(str(target), {"a": 1})
    assert modes_before_replace == [PRIVATE]
    assert mode_of(target) == PRIVATE


def test_each_write_uses_its_own_temporary_file(tmp_path, monkeypatch):
    target = tmp_path / "sessions.json"
    sources = []
    real_replace = os.replace

    def recording_replace(source, destination):
        sources.append(source)
        real_replace(source, destination)

    monkeypatch.setattr(atomic_io.os, "replace", recording_replace)
    atomic_io.atomic_write_json(str(target), [1])
    atomic_io.atomic_write_json(str(target), [2])
    assert len(set(sources)) == 2
    assert sorted(os.listdir(tmp_path)) == ["sessions.json"]


def test_a_failed_write_leaves_no_temporary_file(tmp_path):
    target = tmp_path / "notes.json"
    with pytest.raises(TypeError):
        atomic_io.atomic_write_json(str(target), {"bad": object()})
    assert os.listdir(tmp_path) == []


def test_the_encryption_key_file_is_created_private(tmp_path, monkeypatch, open_umask):
    created_modes = []
    real_open = os.open

    def recording_open(path, flags, mode=0o777, **kwargs):
        descriptor = real_open(path, flags, mode, **kwargs)
        if str(path).endswith(".key"):
            created_modes.append(mode_of(path))
        return descriptor

    monkeypatch.setattr(os, "open", recording_open)
    APIKeyManager(str(tmp_path)).get_or_create_key()
    assert created_modes == [PRIVATE]
    assert mode_of(tmp_path / ".key") == PRIVATE
