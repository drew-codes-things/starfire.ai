import os
import re
from collections import Counter
from html.parser import HTMLParser

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static")
PAGES = ["index.html", "login.html", "setup.html"]
APP_JS_PARTS = ["appState.js", "chatMessages.js", "codeBlocks.js", "chatCards.js", "chatSend.js", "helperTasks.js",
                "settings.js", "settingsMemoryTasks.js", "settingsEmail.js", "settingsNotes.js", "settingsSystem.js",
                "compare.js", "palette.js", "app.js"]
APP_SCRIPTS = [*APP_JS_PARTS, "workspace.js", "uiLogic.js", "login.js", "setup.js"]
IDS_BUILT_BY_SCRIPTS = {"emailAiResult", "emailReplyBox", "ollamaStartBtn", "ollamaStartMsg", "welcomeConnectBtn"}
UNLABELLED_TYPES = {"hidden"}
HEX_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
TOKEN_BLOCK = re.compile(r"^(?::root|html\.light) \{.*?^\}", re.S | re.M)
MOTION_DECLARATION = re.compile(r"(?<![\w-])(?:transition|animation): ([^;]+);")
RAW_TIMING = re.compile(r"(?<![\w.-])(?:\d*\.\d+|[1-9]\d*)m?s\b|(?<![\w-])(?:ease|ease-out|ease-in|linear)(?![\w-])")
LOOPING = "infinite"
RADIUS_DECLARATION = re.compile(r"border-radius: ([^;]+);")
RADIUS_PART = re.compile(r"var\(--r-(?:xs|sm|md|lg|xl|pill)\)|0|50%")


def read(name: str) -> str:
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def read_app() -> str:
    return "".join(read(name) for name in APP_JS_PARTS)


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


def test_the_chat_page_loads_the_app_parts_in_order_with_app_js_last():
    scripts = re.findall(r'<script src="([^"]+)"', read("index.html"))
    first = scripts.index(APP_JS_PARTS[0])
    assert scripts[first:first + len(APP_JS_PARTS)] == APP_JS_PARTS
    assert scripts.index("sideChat.js") < first < scripts.index("workspace.js")


def test_scripts_only_look_up_elements_that_exist():
    ids = set(Page(read("index.html")).ids)
    scripts = read_app() + read("workspace.js")
    wanted = set(re.findall(r"\$\('([A-Za-z0-9_]+)'\)", scripts))
    wanted |= set(re.findall(r"getElementById\('([A-Za-z0-9_]+)'\)", scripts))
    missing = sorted(wanted - ids - IDS_BUILT_BY_SCRIPTS)
    assert not missing, f"app.js or workspace.js look up missing elements: {missing}"


def test_icons_used_by_pages_and_scripts_are_in_the_sprite():
    html = read("index.html")
    defined = set(re.findall(r'<symbol id="i-([a-z-]+)"', html))
    used = set(re.findall(r'href="#i-([a-z-]+)"', html))
    scripts = read_app() + read("workspace.js")
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


def test_one_shot_motion_uses_the_shared_timing_tokens():
    rules = TOKEN_BLOCK.sub("", read("style.css"))
    raw = [value for value in MOTION_DECLARATION.findall(rules) if LOOPING not in value and RAW_TIMING.search(value)]
    assert not raw, f"transitions and animations should use --dur, --dur-rise and --ease: {raw}"


def test_corner_radii_come_from_tokens():
    values = RADIUS_DECLARATION.findall(TOKEN_BLOCK.sub("", read("style.css")))
    raw = [value for value in values if not all(RADIUS_PART.fullmatch(part) for part in value.split())]
    assert not raw, f"border-radius should use the --r-* tokens: {raw}"


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
    fallback = re.search(r"own_tools_models: \[([^\]]*)\]", read_app())
    assert fallback, "app.js has no own_tools_models fallback"
    names = re.findall(r"'([^']+)'", fallback.group(1))
    assert {"Arty", "assistant-core", "arty/*"} == set(names)


NARROW_BLOCK = re.compile(r"@media \(max-width: 860px\) \{(.*?)^\}", re.S | re.M)
PHONE_BLOCK = re.compile(r"@media \(max-width: 600px\) \{(.*?)^\}", re.S | re.M)


def test_the_chat_controls_get_a_backdrop_on_small_screens_that_closes_them():
    assert 'id="controlsScrim"' in read("index.html")
    assert ".controls-scrim:not([hidden])" in NARROW_BLOCK.search(read("style.css")).group(1)
    workspace = read("workspace.js")
    assert "$('controlsScrim').hidden = panel.hidden || !isNarrowScreen();" in workspace
    assert "$('controlsScrim').onclick = () => toggleControls(false);" in workspace


def test_toasts_sit_at_the_top_so_they_never_cover_the_message_box():
    toasts = re.search(r"^\.toasts \{(.*?)^\}", read("style.css"), re.S | re.M).group(1)
    assert "top:" in toasts and "bottom:" not in toasts
    assert ".toasts" not in PHONE_BLOCK.search(read("style.css")).group(1)


def test_narrow_settings_fields_outrank_the_rule_that_makes_fields_share_a_row():
    css = read("style.css")
    assert ".settings-row > input.field-narrow { flex: 0 1 96px; }" in css
    assert ".settings-row > input.field-medium { flex: 0 1 168px; }" in css


def test_the_phone_arty_cli_live_view_drops_the_brand_and_fills_the_screen():
    phone = PHONE_BLOCK.search(read("style.css")).group(1)
    assert ".arty-cli-live .login-brand, .arty-cli-live .arty-back { display: none; }" in phone
    assert "body.login-page:has(.arty-cli-live) { padding: 0; }" in phone


def css_rule(selector: str, css: str | None = None) -> str:
    match = re.search(rf"^{re.escape(selector)} \{{(.*?)\}}", css if css is not None else read("style.css"), re.S | re.M)
    assert match, f"style.css has no rule for {selector}"
    return match.group(1)


def test_the_default_model_star_is_a_quiet_filled_star_not_a_red_disc():
    star = css_rule("#setDefaultModelBtn.active")
    assert "background: transparent;" in star
    assert "color: var(--text);" in star
    assert "accent" not in star


def test_on_phones_the_reply_actions_stay_on_one_row_and_the_model_pick_sits_on_the_regenerate_button():
    phone = PHONE_BLOCK.search(read("style.css")).group(1)
    assert "flex-wrap: nowrap;" in css_rule("  .line-actions", phone)
    assert "flex: 0 1 var(--touch);" in css_rule("  .line-action-btn", phone)
    picker = css_rule("  .regen-group .regen-model-select", phone)
    assert "position: absolute;" in picker and "opacity: 0;" in picker
    app = read_app()
    assert "group.className = 'regen-group';" in app
    assert "select.onchange = () => { if (regeneratePicksAndRuns()) regenerate(); };" in app
    assert "REGENERATE_WITH" in app


def test_opening_a_settings_tab_shows_loading_in_its_empty_lists():
    app = read_app()
    listed = re.search(r"const SETTINGS_TAB_LISTS = \{(.*?)\};", app, re.S)
    assert listed, "app.js has no SETTINGS_TAB_LISTS"
    ids = set(re.findall(r"'([A-Za-z]+List)'", listed.group(1)))
    assert {"memoryList", "documentList", "endpointList", "mcpServerList", "presetList", "promptList", "noteList",
            "taskList", "emailAccountList", "ruleList", "workflowList", "accountList"} <= ids
    assert ids <= set(Page(read("index.html")).ids)
    show_tab = re.search(r"function showSettingsTab\(tab\) \{(.*?)\n\}", app, re.S).group(1)
    assert "showListLoading" in show_tab


def test_adding_a_memory_reports_a_failure_under_the_field():
    html = read("index.html")
    assert re.search(r'<p class="settings-hint" id="memoryAddResult" role="status"></p>', html)
    handler = re.search(r"settingsEls\.addMemoryEntryBtn\.onclick = async \(\) => \{(.*?)\n\};", read_app(), re.S)
    assert handler, "no memory add handler"
    body = handler.group(1)
    assert "if (!r.ok) throw new Error(await failureMessage(r, " in body
    assert "settingsEls.memoryAddResult.className = 'settings-hint error';" in body



def test_the_rule_account_select_is_filled_from_the_accounts_or_says_there_are_none():
    app = read_app()
    assert "const choices = ruleAccountChoices(accounts);" in app
    assert "settingsEls.ruleAccountSelect.disabled = !choices.usable;" in app
    assert "settingsEls.createRuleBtn.disabled = !choices.usable;" in app
