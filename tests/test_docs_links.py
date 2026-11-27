import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELATIVE_LINK = re.compile(r"\]\(([^)\s]+)\)")
HEADING = re.compile(r"^#+\s*(.*)$", re.MULTILINE)
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:")
VENDORED_FOLDER = "static/vendor/"
DOCS_FOLDER = "docs"
PAGES_NOT_IN_README_TABLE = {"README.md"}


def tracked_markdown_files() -> list[str]:
    if not os.path.exists(os.path.join(ROOT, ".git")):
        return []
    names = subprocess.run(["git", "-C", ROOT, "ls-files", "*.md"], check=True, capture_output=True,
                           text=True).stdout.split()
    return [name for name in names if not name.startswith(VENDORED_FOLDER)]


def read(relative_path: str) -> str:
    with open(os.path.join(ROOT, relative_path), encoding="utf-8") as handle:
        return handle.read()


def anchor_of(heading: str) -> str:
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def anchors_in(relative_path: str) -> set[str]:
    return {anchor_of(heading) for heading in HEADING.findall(read(relative_path))}


def links_in(relative_path: str) -> list[str]:
    return [link for link in RELATIVE_LINK.findall(read(relative_path)) if not link.startswith(EXTERNAL_PREFIXES)]


def broken_links(relative_path: str) -> list[str]:
    problems = []
    for link in links_in(relative_path):
        target_path, _, anchor = link.partition("#")
        target = os.path.normpath(os.path.join(os.path.dirname(relative_path), target_path)) if target_path else relative_path
        if not os.path.exists(os.path.join(ROOT, target)):
            problems.append(f"{relative_path}: {link} points at a missing file")
        elif anchor and target.endswith(".md") and anchor not in anchors_in(target):
            problems.append(f"{relative_path}: {link} points at a missing heading")
    return problems


def docs_pages() -> list[str]:
    return sorted(name for name in os.listdir(os.path.join(ROOT, DOCS_FOLDER)) if name.endswith(".md"))


@pytest.mark.parametrize("relative_path", tracked_markdown_files())
def test_every_relative_link_and_heading_anchor_resolves(relative_path):
    assert broken_links(relative_path) == []


@pytest.mark.parametrize("page", [page for page in docs_pages() if page not in PAGES_NOT_IN_README_TABLE])
def test_readme_and_docs_index_both_link_every_docs_page(page):
    assert f"{DOCS_FOLDER}/{page}" in links_in("README.md")
    assert page in links_in(f"{DOCS_FOLDER}/README.md")


def test_outside_a_git_checkout_no_files_are_listed_so_the_link_check_skips(monkeypatch, tmp_path):
    monkeypatch.setattr(sys.modules[__name__], "ROOT", str(tmp_path))
    assert tracked_markdown_files() == []
