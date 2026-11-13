import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static")


def read(name: str) -> str:
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


def test_the_side_chat_logic_loads_before_the_chat_scripts():
    scripts = re.findall(r'<script src="([^"]+)"', read("index.html"))
    assert "sideChat.js" in scripts
    assert scripts.index("sideChat.js") < scripts.index("app.js")


def test_the_side_chat_banner_has_a_way_back_to_the_main_chat():
    html = read("index.html")
    banner = re.search(r'<div[^>]*id="sideBanner"[^>]*hidden[^>]*>(.*?)</div>', html, re.S)
    assert banner, "index.html has no hidden side chat banner"
    assert 'id="sideChatCloseBtn"' in banner.group(1)


def test_replies_in_a_side_chat_see_the_main_chat():
    app = read("app.js")
    assert "apiMessages(sideChatContext(sideChat, messages))" in app
    assert "apiMessages(sideChatContext(sideChat, messages.slice(0, idx + 1)))" in app


def test_side_chats_are_never_saved():
    workspace = read("workspace.js")
    assert re.search(r"function isTemporaryChat\(\) \{ return tempChatToggle\.checked \|\| isSideChatOpen\(\); \}", workspace)
    fork = re.search(r"async function forkSession\(uptoIdx, textBeingSent\) \{(.*?)\n\}", read("app.js"), re.S)
    assert fork and "isTemporaryChat()" in fork.group(1), "forkSession saves temporary and side chats"


def test_the_composer_and_palette_open_side_chats():
    app = read("app.js")
    workspace = read("workspace.js")
    assert "parseSideChatCommand(" in app
    assert "'Toggle side chat'" in app
    assert "sideChatCommandsMatching(" in workspace


def test_archiving_or_deleting_the_chat_behind_a_side_chat_closes_both():
    workspace = read("workspace.js")
    assert "if (archived && isChatOpen(session.id)) clearChat();" in workspace
    assert "if (isChatOpen(session.id)) clearChat();" in workspace
    assert "sessionId === sideChat.mainSessionId" in read("app.js")


def test_only_the_main_chat_asks_for_remember_commands():
    app = read("app.js")
    assert app.count("memory_commands:") == 1
    assert "memory_commands: !isTemporaryChat()," in app
