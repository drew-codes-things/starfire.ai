import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import pytest

from api_key_manager import APIKeyManager
from documents_store import DocumentStore


def read_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


BROKEN = "{broken"


def set_aside_copies(path) -> list[str]:
    return sorted(glob.glob(str(path) + ".unreadable-*"))


@pytest.mark.parametrize("content", [BROKEN, "[1, 2]"])
def test_an_unreadable_key_file_is_kept_aside_not_overwritten(tmp_path, content):
    keys = APIKeyManager(str(tmp_path))
    (tmp_path / "api_keys.json").write_text(content)
    keys.save("openai", "sk-new")
    [aside] = set_aside_copies(tmp_path / "api_keys.json")
    assert read_text(aside) == content
    assert keys.load() == {"openai": "sk-new"}


@pytest.mark.parametrize("content", [BROKEN, "[1, 2]"])
def test_an_unreadable_documents_file_is_kept_aside_not_overwritten(tmp_path, content):
    documents = DocumentStore(str(tmp_path))
    (tmp_path / "documents.json").write_text(content)
    assert documents.list() == []
    [aside] = set_aside_copies(tmp_path / "documents.json")
    assert read_text(aside) == content
    documents.add("notes.txt", b"One line. Two lines.")
    assert [d.filename for d in documents.list()] == ["notes.txt"]
    assert read_text(aside) == content
