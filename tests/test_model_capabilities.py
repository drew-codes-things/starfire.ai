import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import model_capabilities as caps


def test_tiers_follow_parameter_count_for_ollama():
    assert caps.tool_tier("ollama", "qwen2.5:1.5b") == "none"
    assert caps.tool_tier("ollama", "llama3.2:3b") == "basic"
    assert caps.tool_tier("ollama", "qwen2.5-coder:7b") == "basic"
    assert caps.tool_tier("ollama", "qwen3:14b") == "full"
    assert caps.tool_tier("ollama", "gemma3:27b") == "full"
    assert caps.tool_tier("ollama", "codellama:34b") == "none"
    assert caps.tool_tier("ollama", "mystery-model") == "full"


def test_hosted_providers_always_get_everything():
    assert caps.tool_tier("openai", "gpt-4o-mini") == "full"
    assert caps.tool_tier("anthropic", "claude-3-haiku") == "full"


def test_basic_tier_keeps_only_simple_tools_and_caps_the_count():
    enabled = {"search_web", "run_shell", "manage_email", "manage_notes", "deep_research", "find_image"}
    assert caps.tools_for_tier("basic", enabled) == {"search_web", "manage_notes", "find_image"}
    assert caps.tools_for_tier("full", enabled) == enabled
    assert caps.tools_for_tier("none", enabled) == set()
    assert caps.tool_cap_for_tier("basic") == caps.BASIC_TIER_MAX_TOOLS
    assert caps.tool_cap_for_tier("full") is None
    assert "basic tool set" in caps.tier_description("basic")
