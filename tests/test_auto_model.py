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
    assert picks == {"vision": VISION, "code": CODER, "agent": ARTY, "chat": GENERAL}


@pytest.mark.parametrize("missing, role", [(CODER, "code"), (ARTY, "agent"), (VISION, "vision")])
def test_a_role_whose_model_is_missing_falls_back_to_the_chat_model(missing, role):
    usable = [model(name) for name in LISTED if name != missing]
    pick = auto_model.choose(role, usable, sources())
    assert (pick.model, pick.role, pick.filled_by) == (GENERAL, role, "chat")


def test_the_admins_choice_fills_a_role_and_a_vanished_choice_falls_back_to_the_guess():
    usable = [model(name) for name in LISTED]
    configured = {"chat": SMALL, "code": "gone-model"}
    assert auto_model.choose("chat", usable, sources(configured=configured)).model == SMALL
    assert auto_model.choose("code", usable, sources(configured=configured)).model == CODER


def test_without_a_task_model_the_chat_role_takes_the_first_general_model():
    usable = [model(name) for name in LISTED]
    assert auto_model.choose("chat", usable, sources(task_model="")).model == SMALL


def test_a_guess_never_lands_on_a_restricted_model_even_for_an_admin():
    usable = [model(RESTRICTED), model(VISION)]
    for role in auto_model.ROLES:
        assert auto_model.choose(role, usable, sources()).model == VISION
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
