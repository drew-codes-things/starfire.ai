import os
import re
from collections import Counter
from html.parser import HTMLParser

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static")
PAGES = ["index.html", "login.html", "setup.html"]
APP_SCRIPTS = ["app.js", "workspace.js", "uiLogic.js", "login.js", "setup.js"]
IDS_BUILT_BY_SCRIPTS = {"emailAiResult", "emailReplyBox", "ollamaStartBtn", "ollamaStartMsg", "welcomeConnectBtn"}
UNLABELLED_TYPES = {"hidden"}
HEX_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
TOKEN_BLOCK = re.compile(r"^(?::root|html\.light) \{.*?^\}", re.S | re.M)


def read(name: str) -> str:
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


class Page(HTMLParser):

    def __init__(self, html: str):
        super().__init__()
        self.ids: list[str] = []
        self.labelled_ids: set[str] = set()
        self.controls: list[dict] = []
        self.buttons: list[dict] = []
        self.inside_label = 0
        self.open_button: dict | None = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if "id" in attributes:
            self.ids.append(attributes["id"])
        if tag == "label":
            self.inside_label += 1
            if attributes.get("for"):
                self.labelled_ids.add(attributes["for"])
        if tag in ("input", "select", "textarea"):
            self.controls.append({**attributes, "tag": tag, "wrapped": self.inside_label > 0})
        if tag == "button":
            self.open_button = {**attributes, "text": ""}
            self.buttons.append(self.open_button)

    def handle_endtag(self, tag):
        if tag == "label":
            self.inside_label -= 1
        if tag == "button":
            self.open_button = None

    def handle_data(self, data):
        if self.open_button is not None:
            self.open_button["text"] += data.strip()


@pytest.fixture(scope="module", params=PAGES)
def page(request):
    return request.param, Page(read(request.param))


def test_ids_are_unique(page):
    name, parsed = page
    repeated = [i for i, count in Counter(parsed.ids).items() if count > 1]
    assert not repeated, f"{name} repeats ids {repeated}"


def test_every_form_control_has_a_name_for_screen_readers(page):
    name, parsed = page
    unnamed = [c.get("id") or c.get("name") or c["tag"] for c in parsed.controls
               if c.get("type") not in UNLABELLED_TYPES and "aria-hidden" not in c
               and not (c.get("aria-label") or c.get("aria-labelledby") or c["wrapped"]
                        or c.get("id") in parsed.labelled_ids)]
    assert not unnamed, f"{name} has controls with no label: {unnamed}"


def test_every_button_has_a_name_for_screen_readers(page):
    name, parsed = page
    unnamed = [b.get("id") or b.get("class") for b in parsed.buttons if not (b["text"] or b.get("aria-label"))]
    assert not unnamed, f"{name} has buttons with no text or aria-label: {unnamed}"


def test_theme_is_applied_before_the_page_paints(page):
    name, _ = page
    html = read(name)
    head = html.split("</head>")[0]
    assert "localStorage.getItem('sf_theme')" in head, f"{name} sets the theme too late"
    assert "<script src=" not in head, f"{name} blocks the first paint on a script download"
    assert 'name="theme-color"' in head


def test_scripts_only_look_up_elements_that_exist():
    ids = set(Page(read("index.html")).ids)
    scripts = read("app.js") + read("workspace.js")
    wanted = set(re.findall(r"\$\('([A-Za-z0-9_]+)'\)", scripts))
    wanted |= set(re.findall(r"getElementById\('([A-Za-z0-9_]+)'\)", scripts))
    missing = sorted(wanted - ids - IDS_BUILT_BY_SCRIPTS)
    assert not missing, f"app.js or workspace.js look up missing elements: {missing}"


def test_icons_used_by_pages_and_scripts_are_in_the_sprite():
    html = read("index.html")
    defined = set(re.findall(r'<symbol id="i-([a-z-]+)"', html))
    used = set(re.findall(r'href="#i-([a-z-]+)"', html))
    scripts = read("app.js") + read("workspace.js")
    used |= set(re.findall(r"icon\('([a-z-]+)'", scripts))
    action_icons = re.search(r"const ACTION_ICONS = \{(.*?)\};", scripts, re.S).group(1)
    used |= set(re.findall(r"\['([a-z-]+)'", action_icons))
    assert used <= defined, f"icons missing from the sprite: {sorted(used - defined)}"


@pytest.mark.parametrize("script", APP_SCRIPTS)
def test_scripts_use_starfire_dialogs_not_browser_ones(script):
    native = re.findall(r"(?<![.\w])(?:alert|confirm|prompt)\(", read(script))
    assert not native, f"{script} still opens a browser dialog"


def test_colours_come_from_tokens():
    rules = TOKEN_BLOCK.sub("", read("style.css"))
    stray = HEX_COLOUR.findall(rules)
    assert not stray, f"style.css has colours outside the token blocks: {stray}"


def test_both_themes_define_the_same_colour_roles():
    css = read("style.css")
    dark = re.search(r"^:root \{(.*?)^\}", css, re.S | re.M).group(1)
    light = re.search(r"^html\.light \{(.*?)^\}", css, re.S | re.M).group(1)
    for role in ("--bg", "--bg-sunken", "--surface", "--raised", "--text", "--muted", "--faint",
                 "--accent-text", "--ok", "--warn", "--scrim", "--shadow-1", "--shadow-2"):
        assert f"{role}:" in dark and f"{role}:" in light, f"{role} is not set in both themes"


def relative_luminance(colour: str) -> float:
    channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: str, second: str) -> float:
    lighter, darker = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def theme_tokens(selector: str) -> dict[str, str]:
    block = re.search(rf"^{re.escape(selector)} \{{(.*?)^\}}", read("style.css"), re.S | re.M).group(1)
    return dict(re.findall(r"(--[a-z-]+): (#[0-9a-fA-F]{6});", block))


@pytest.mark.parametrize("selector", [":root", "html.light"])
def test_text_colours_meet_wcag_aa_on_every_surface(selector):
    tokens = {**theme_tokens(":root"), **theme_tokens(selector)}
    minimum = 4.5
    for text in ("--text", "--muted", "--faint", "--accent-text", "--ok", "--warn"):
        for surface in ("--bg", "--bg-sunken", "--surface", "--raised"):
            ratio = contrast(tokens[text], tokens[surface])
            assert ratio >= minimum, f"{selector}: {text} on {surface} is {ratio:.2f}:1"
    assert contrast(tokens["--on-accent"], tokens["--accent-fill"]) >= minimum


def test_the_offline_fallback_treats_arty_and_its_old_name_as_bringing_their_own_tools():
    fallback = re.search(r"own_tools_models: \[([^\]]*)\]", read("app.js"))
    assert fallback, "app.js has no own_tools_models fallback"
    names = re.findall(r"'([^']+)'", fallback.group(1))
    assert {"Arty", "assistant-core", "arty/*"} == set(names)
