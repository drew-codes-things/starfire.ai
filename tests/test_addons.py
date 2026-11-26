import os
import sys
import types
from unittest import mock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import addons
import auth
import static_files

STATIC = os.path.join(ROOT, "static")
SLOT_NAMES = ("settings-tabs", "settings-panels", "scripts")


def missing_addon(name, *rest, **named):
    raise ModuleNotFoundError(f"No module named '{name}'", name=name)


@pytest.fixture
def admin_prefixes():
    before = list(auth.admin_only_addon_prefixes)
    yield auth.admin_only_addon_prefixes
    auth.admin_only_addon_prefixes[:] = before


def test_no_add_on_folder_means_nothing_is_installed(admin_prefixes):
    app = FastAPI()
    with mock.patch("importlib.import_module", side_effect=missing_addon):
        assert addons.install(app) is False
    assert [route.path for route in app.routes if route.path not in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc")] == []
    assert admin_prefixes == []


def test_a_module_missing_inside_the_add_on_is_an_error():
    broken = ModuleNotFoundError("No module named 'other'", name="other")
    with mock.patch("importlib.import_module", side_effect=broken), pytest.raises(ModuleNotFoundError):
        addons.load()


def test_an_add_on_with_routers_and_prefixes_is_installed(admin_prefixes):
    router = APIRouter(prefix="/api/extra")

    @router.get("")
    async def extra():
        return {"ok": True}

    addon = types.SimpleNamespace(routers=(router,), admin_only_prefixes=("/api/extra",))
    app = FastAPI()
    with mock.patch("importlib.import_module", return_value=addon):
        assert addons.install(app) is True
    assert TestClient(app).get("/api/extra").json() == {"ok": True}
    assert auth.is_admin_only("GET", "/api/extra")


def test_an_add_on_with_nothing_to_offer_changes_nothing(admin_prefixes):
    with mock.patch("importlib.import_module", return_value=types.SimpleNamespace()):
        assert addons.install(FastAPI()) is True
    assert admin_prefixes == []


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


@pytest.fixture
def pages(tmp_path):
    write(str(tmp_path / "index.html"), "<nav>\n" + "".join(f'<template data-addon-slot="{name}"></template>\n' for name in SLOT_NAMES) + "</nav>\n")
    write(str(tmp_path / "other.html"), '<template data-addon-slot="scripts"></template>')
    return tmp_path


def served(directory, path="/"):
    app = FastAPI()
    app.mount("/", static_files.compressed_static(str(directory)))
    return TestClient(app).get(path)


def test_without_add_on_files_the_slots_are_simply_gone(pages):
    page = served(pages).text
    assert page == "<nav>\n</nav>\n"


def test_add_on_files_fill_their_slots(pages):
    write(str(pages / "local_addon" / "settings-tabs.html"), "<b>tab</b>\n")
    write(str(pages / "local_addon" / "scripts.html"), "<i>script</i>\n")
    assert served(pages).text == "<nav>\n<b>tab</b>\n<i>script</i>\n</nav>\n"
    assert served(pages, "/index.html").text == served(pages).text


def test_only_the_home_page_has_slots_filled(pages):
    write(str(pages / "local_addon" / "scripts.html"), "<i>script</i>\n")
    assert served(pages, "/other.html").text == '<template data-addon-slot="scripts"></template>'


def test_other_files_are_served_as_they_are(pages):
    write(str(pages / "app.js"), "let x = 1;\n")
    assert served(pages, "/app.js").text == "let x = 1;\n"
    assert served(pages, "/missing.js").status_code == 404


def test_the_shipped_home_page_has_every_slot_and_no_tab_of_its_own():
    with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as handle:
        page = handle.read()
    for name in SLOT_NAMES:
        assert page.count(f'<template data-addon-slot="{name}"></template>') == 1
    assert "data-tab=\"room\"" not in page
    assert "data-panel=\"room\"" not in page
