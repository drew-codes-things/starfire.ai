import logging
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import auto_model
import main_model as main_model_setting
import routes
from config import config

MAIN = "hf.co/owner/big-a3b-gguf:iq4_xs"
SMALL = "qwen3:14b"


@pytest.fixture
def listing(monkeypatch, tmp_path, caplog):
    seen = {}
    monkeypatch.setattr(config, "task_model", MAIN)
    monkeypatch.setattr(main_model_setting, "_local_listing", [lambda: seen.get("local")])
    main_model_setting._report_unusable.cache_clear()
    caplog.set_level(logging.WARNING)
    return seen


def saved_default(name: str) -> None:
    auto_model.AutoModelSettings(config.data_dir).set_roles({auto_model.CHAT: name})


def warnings(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]


def test_a_saved_default_waits_quietly_for_the_first_listing(listing, caplog):
    saved_default(SMALL)
    assert main_model_setting.main_model() == MAIN
    listing["local"] = {SMALL, MAIN}
    assert main_model_setting.main_model() == SMALL
    assert warnings(caplog) == []


def test_a_saved_default_missing_from_a_known_listing_is_reported_once(listing, caplog):
    listing["local"] = {SMALL, MAIN}
    saved_default("Arty")
    assert [main_model_setting.main_model() for _ in range(3)] == [MAIN, MAIN, MAIN]
    assert len(warnings(caplog)) == 1 and "'Arty'" in warnings(caplog)[0]


def test_a_blank_saved_default_is_no_setting(listing, caplog):
    listing["local"] = {SMALL}
    saved_default("   ")
    assert main_model_setting.main_model() == MAIN and warnings(caplog) == []


def test_the_connection_health_monitor_remembers_lists_in_the_test_folder(tmp_path):
    folder = os.path.dirname(routes.connection_health._remembered_path)
    assert os.path.realpath(folder) == os.path.realpath(str(tmp_path))
