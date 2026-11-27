import base64
import os
import random
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import auto_model

VISION, GENERAL, SMALL, CODER, ARTY, RESTRICTED = (
    "vision-vl:7b", "general:14b", "small-chat:9b", "coder:7b", "Arty", "restricted-abl")
LISTED = [VISION, RESTRICTED, SMALL, CODER, GENERAL, ARTY]


def model(name, endpoint_id="ep"):
    return {"id": name, "endpoint_id": endpoint_id}


def sources(**overrides):
    defaults = dict(configured={}, task_model=GENERAL, own_tools_patterns=["Arty", "assistant-core", "arty/*"],
                    restricted_patterns=["*-abl", "*-abl-*", "*abliterated*"])
    return auto_model.RoleSources(**{**defaults, **overrides})


@pytest.mark.parametrize("text, kinds, role", [
    ("what is a good name for a cat", set(), auto_model.CHAT),
    ("tell me about the history of tea", set(), auto_model.CHAT),
    ("```python\nprint('hi')\n```\nwhy does this print twice?", set(), auto_model.CODE),
    ("Traceback (most recent call last):\n  File \"x\", line 1\nValueError: bad", set(), auto_model.CODE),
    ("TypeError: cannot read properties of undefined", set(), auto_model.CODE),
    ("fix utils.py so it stops crashing on empty input", set(), auto_model.CODE),
    ("write a function that reverses a list", set(), auto_model.CODE),
    ("how do I write a regex for dates", set(), auto_model.CODE),
    ("remind me to call the dentist at 5", set(), auto_model.AGENT),
    ("hey arty, what's on my calendar", set(), auto_model.AGENT),
    ("Arty play some jazz", set(), auto_model.AGENT),
    ("search the web for train times", set(), auto_model.AGENT),
    ("describe this", {"image"}, auto_model.VISION),
    ("```js\nx()\n```", {"image"}, auto_model.VISION),
    ("what happens in this clip", {"video"}, auto_model.VISION),
    ("transcribe this", {"audio"}, auto_model.AGENT),
    ("it crashed with\n    at run(server:12:5)", set(), auto_model.CODE),
    ("\n\n\tat Worker.handle(worker:3)", set(), auto_model.CODE),
    ("why is src/api-client/index.ts so slow", set(), auto_model.CODE),
    ("open my-notes/plan.yml please", set(), auto_model.CODE),
])
def test_each_prompt_gets_the_role_its_rules_say(text, kinds, role):
    assert auto_model.prompt_role(text, kinds) == role


def test_every_role_has_a_sensible_model_without_any_admin_settings():
    usable = [model(name) for name in LISTED]
    picks = {role: auto_model.choose(role, usable, sources()).model for role in auto_model.ROLES}
    assert picks == {"vision": VISION, "code": GENERAL, "agent": ARTY, "chat": GENERAL}


def test_the_coding_model_follows_the_default_model_until_an_admin_names_one():
    usable = [model(name) for name in LISTED]
    assert auto_model.choose("code", usable, sources()).model == GENERAL
    assert auto_model.choose("code", usable, sources(configured={"chat": SMALL})).model == SMALL
    assert auto_model.choose("code", usable, sources(configured={"chat": SMALL, "code": CODER})).model == CODER


def test_no_guess_ever_lands_on_an_outside_connection():
    usable = [model(name, "remote-a") for name in (VISION, CODER, GENERAL, ARTY, SMALL)] + [model("local-chat:9b")]
    outside = sources(task_model="", outside=lambda m: m["endpoint_id"] == "remote-a")
    for role in auto_model.ROLES:
        pick = auto_model.choose(role, usable, outside)
        assert (pick.endpoint_id, pick.model) == ("ep", "local-chat:9b"), role
    assert auto_model.vision_candidates(usable, outside) == []


def test_only_outside_models_leave_auto_with_nothing_to_guess():
    usable = [model(name, "remote-a") for name in (VISION, GENERAL, ARTY)]
    outside = sources(outside=lambda m: m["endpoint_id"] == "remote-a")
    assert all(auto_model.choose(role, usable, outside) is None for role in auto_model.ROLES)


def test_an_outside_model_the_admin_names_is_used():
    usable = [model(GENERAL, "remote-a"), model(SMALL)]
    outside = sources(configured={"chat": GENERAL, "code": GENERAL}, outside=lambda m: m["endpoint_id"] == "remote-a")
    assert (auto_model.choose("chat", usable, outside).endpoint_id, auto_model.choose("code", usable, outside).model) == \
        ("remote-a", GENERAL)


@pytest.mark.parametrize("missing, role", [(ARTY, "agent"), (VISION, "vision")])
def test_a_role_whose_model_is_missing_falls_back_to_the_chat_model(missing, role):
    usable = [model(name) for name in LISTED if name != missing]
    pick = auto_model.choose(role, usable, sources())
    assert (pick.model, pick.role, pick.filled_by) == (GENERAL, role, "chat")


def test_the_admins_choice_fills_a_role_and_a_vanished_choice_falls_back_to_the_guess():
    usable = [model(name) for name in LISTED]
    configured = {"chat": SMALL, "code": "gone-model"}
    assert auto_model.choose("chat", usable, sources(configured=configured)).model == SMALL
    assert auto_model.choose("code", usable, sources(configured=configured)).model == SMALL


def test_without_a_task_model_the_chat_role_takes_the_first_general_model():
    usable = [model(name) for name in LISTED]
    assert auto_model.choose("chat", usable, sources(task_model="")).model == SMALL


def test_a_guess_never_lands_on_a_restricted_model_even_for_an_admin():
    usable = [model(RESTRICTED), model(VISION)]
    for role in auto_model.ROLES:
        assert auto_model.choose(role, usable, sources(task_model="")).model == VISION
    assert auto_model.choose("chat", [model(RESTRICTED)], sources()) is None


def test_role_settings_only_take_known_roles_and_short_names():
    assert auto_model.cleaned_roles({"chat": "  x  ", "code": None}) == {"chat": "x", "code": ""}
    with pytest.raises(auto_model.AutoModelError):
        auto_model.cleaned_roles({"writing": "x"})
    with pytest.raises(auto_model.AutoModelError):
        auto_model.cleaned_roles({"chat": "x" * (auto_model.MAX_MODEL_NAME_CHARS + 1)})


def test_a_restricted_model_listed_with_a_tag_is_never_chosen_even_as_the_last_resort():
    usable = [model("restricted-abl:latest"), model("library/other-abl:q4")]
    assert all(auto_model.choose(role, usable, sources()) is None for role in auto_model.ROLES)


def base64url_paste(chars):
    return base64.urlsafe_b64encode(random.Random(0).randbytes(chars)).decode()[:chars]


LINEAR_PATTERN_INPUT_CHARS = 64_000
SECONDS_FOR_ONE_PATTERN = 0.5
SECONDS_FOR_ONE_MESSAGE = 0.25
HOSTILE_PATTERN_INPUTS = {
    "words joined by hyphens": "a-" * (LINEAR_PATTERN_INPUT_CHARS // 2),
    "path-like text": "a/" * (LINEAR_PATTERN_INPUT_CHARS // 2),
    "blank lines": "\n" * LINEAR_PATTERN_INPUT_CHARS,
    "spaces and newlines": " \n" * (LINEAR_PATTERN_INPUT_CHARS // 2),
    "base64url paste": base64url_paste(LINEAR_PATTERN_INPUT_CHARS),
}
ALL_PATTERNS = [*auto_model.CODE_SIGNS, auto_model.AGENT_SIGNS, auto_model.ADDRESSED_TO_ARTY]


@pytest.mark.parametrize("label", HOSTILE_PATTERN_INPUTS)
@pytest.mark.parametrize("pattern", ALL_PATTERNS, ids=lambda pattern: pattern.pattern[:24])
def test_every_classifier_pattern_takes_time_in_step_with_the_text_length(pattern, label):
    started = time.perf_counter()
    pattern.search(HOSTILE_PATTERN_INPUTS[label])
    assert time.perf_counter() - started < SECONDS_FOR_ONE_PATTERN


REVIEWERS_MESSAGES = {
    "24,000 times a-": "a-" * 24_000,
    "blank lines": "\n" * 48_000,
    "200,000 characters of base64url": base64url_paste(200_000),
    "470 KB": "a-" * (470 * 1024 // 2),
}


@pytest.mark.parametrize("label", REVIEWERS_MESSAGES)
def test_auto_classifies_a_huge_or_hostile_message_in_milliseconds(label):
    started = time.perf_counter()
    assert auto_model.prompt_role(REVIEWERS_MESSAGES[label], set()) == auto_model.CHAT
    assert time.perf_counter() - started < SECONDS_FOR_ONE_MESSAGE


def test_only_the_start_and_the_end_of_a_long_message_decide_its_role():
    prose = "The quick brown fox jumps over the lazy dog. " * 4_000
    assert auto_model.prompt_role("```\n" + prose, set()) == auto_model.CODE
    assert auto_model.prompt_role(prose + "\n```", set()) == auto_model.CODE
    middle = len(prose) // 2
    assert auto_model.prompt_role(prose[:middle] + "\n```\n" + prose[middle:], set()) == auto_model.CHAT
    assert auto_model.prompt_role("Arty, " + prose, set()) == auto_model.AGENT


def test_a_named_model_on_both_a_local_and_a_remote_connection_is_taken_from_the_local_one():
    for listed in ([{"id": "shared-name:14b", "endpoint_id": "far"}, {"id": "shared-name:14b", "endpoint_id": "home"}],
                   [{"id": "shared-name:14b", "endpoint_id": "home"}, {"id": "shared-name:14b", "endpoint_id": "far"}]):
        for role in (auto_model.CHAT, auto_model.VISION):
            sources = auto_model.RoleSources(configured={role: "shared-name:14b"}, task_model="",
                                             outside=lambda model: model["endpoint_id"] == "far")
            assert auto_model.choose(role, listed, sources).endpoint_id == "home"
            assert [m["endpoint_id"] for m in auto_model.vision_candidates(listed, sources)][:1] == (
                ["home"] if role == auto_model.VISION else [])
    only_far = [{"id": "far-only:14b", "endpoint_id": "far"}]
    sources = auto_model.RoleSources(configured={auto_model.CHAT: "far-only:14b"}, outside=lambda model: True)
    assert auto_model.choose(auto_model.CHAT, only_far, sources).endpoint_id == "far"


def test_auto_never_guesses_another_model_when_the_main_is_missing():
    usable = [model(name) for name in LISTED if name != GENERAL]
    assert all(auto_model.choose(role, usable, sources()) is None for role in ("chat", "code"))


def test_a_missing_default_model_is_never_replaced_by_the_task_model_or_the_first_model():
    usable = [model(name) for name in LISTED]
    assert auto_model.choose("chat", usable, sources(configured={"chat": "gone-main:35b"}, task_model=SMALL)) is None


def test_a_hidden_picture_helper_is_never_guessed_for_pictures():
    usable = [model(name) for name in LISTED]
    pick = auto_model.choose("vision", usable, sources(hidden_patterns=["*vl*"]))
    assert (pick.model, pick.filled_by) == (GENERAL, "chat")


def test_the_agent_guess_takes_only_arty_itself_never_a_smaller_model_behind_arty():
    usable = [model("arty/qwen3:14b"), model(GENERAL)]
    pick = auto_model.choose("agent", usable, sources())
    assert (pick.model, pick.filled_by) == (GENERAL, "chat")
