"""Regression checks for the versioned ElevenLabs Vimo agent configuration."""

import json
import importlib.util
from pathlib import Path

import pytest


CONFIG_PATH = Path(__file__).parents[1] / "agent_configs" / "vimo-live.json"
PUBLISH_PATH = Path(__file__).parents[1] / "scripts" / "publish_vimo_agent.py"


def live_config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def publish_module():
    spec = importlib.util.spec_from_file_location("publish_vimo_agent", PUBLISH_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_vimo_does_not_force_a_silence_cutoff() -> None:
    """Let turn detection finish a buyer's thought instead of a fixed timer."""
    turn = live_config()["conversation_config"]["turn"]

    assert turn["turn_timeout"] == -1
    assert turn["turn_eagerness"] == "normal"
    assert turn["speculative_turn"] is False


def test_vimo_uses_its_explicit_conversation_rules() -> None:
    """The platform default personality must not override Vimo's wake discipline."""
    prompt = live_config()["conversation_config"]["agent"]["prompt"]

    assert prompt["ignore_default_personality"] is True


def test_publish_script_only_updates_vimo_prompt_tools_and_turn_settings() -> None:
    config = live_config()
    patch = publish_module().turn_patch(config)
    prompt = config["conversation_config"]["agent"]["prompt"]

    assert patch == {
        "conversation_config": {
            "agent": {"prompt": {"ignore_default_personality": True, "prompt": prompt["prompt"], "max_tokens": 1024}},
            "turn": {"turn_timeout": -1, "turn_eagerness": "normal", "speculative_turn": False},
        },
    }


def test_vimo_can_plan_and_order_a_last_minute_event() -> None:
    prompt = live_config()["conversation_config"]["agent"]["prompt"]
    tools = {t["name"]: t for t in prompt["tools"]}

    assert {"plan_event", "order_event_plan"} <= tools.keys()
    ingredient = tools["plan_event"]["parameters"]["properties"]["recipes"]["items"]["properties"]["ingredients"]["items"]
    assert ingredient["required"] == ["product_intent", "quantity_per_serving", "unit"]  # matches RecipeIngredient
    assert "plan_event" in prompt["prompt"] and "order_event_plan" in prompt["prompt"]


def test_publish_script_refuses_a_non_live_branch() -> None:
    with pytest.raises(ValueError, match="100% live traffic"):
        publish_module().require_fully_live({"current_live_percentage": 1})

    publish_module().require_fully_live({"current_live_percentage": 100})


def test_publish_plans_tool_updates_by_name_ignoring_platform_defaults() -> None:
    tools = live_config()["conversation_config"]["agent"]["prompt"]["tools"]
    by_name = {t["name"]: t for t in tools}
    live = {
        "get_backend_state": ("t1", {**by_name["get_backend_state"], "execution_mode": "immediate"}),  # platform default only
        "create_order_draft": ("t2", {**by_name["create_order_draft"], "description": "older"}),
    }
    plan = {tool["name"]: (action, tool_id) for action, tool, tool_id in publish_module().plan_tools(tools, live)}

    assert plan["get_backend_state"] == ("keep", "t1")
    assert plan["create_order_draft"] == ("update", "t2")
    assert plan["plan_event"] == ("create", None)


def test_vimo_refuses_off_topic_requests() -> None:
    prompt = live_config()["conversation_config"]["agent"]["prompt"]["prompt"]
    assert "# Scope" in prompt and "I can only help with purchasing for your kitchen." in prompt
    assert prompt.index("# Scope") < prompt.index("# Response discipline")  # read before anything else is answered


def test_vimo_has_room_for_a_full_menu_tool_call() -> None:
    """At 80 max_tokens Gemini truncated plan_event arguments, failed the turn, and the fallback only talked."""
    assert live_config()["conversation_config"]["agent"]["prompt"]["max_tokens"] >= 1000


def test_vimo_always_speaks_after_event_tools() -> None:
    """Live, Vimo stayed silent after plan_event returned; the prompt must require a spoken summary."""
    prompt = live_config()["conversation_config"]["agent"]["prompt"]["prompt"]
    assert "As soon as plan_event returns, always speak" in prompt
    assert "never stay silent after it" in prompt


def test_create_body_attaches_tools_by_new_id_not_inline() -> None:
    config = live_config()
    body = publish_module().create_body(config, ["t1", "t2"])
    prompt = body["conversation_config"]["agent"]["prompt"]
    assert "tools" not in prompt and prompt["tool_ids"] == ["t1", "t2"]
    assert prompt["prompt"] == config["conversation_config"]["agent"]["prompt"]["prompt"]
    assert body["platform_settings"]["auth"]["enable_auth"] is True  # sessions only via our signed URL
    assert "tools" in config["conversation_config"]["agent"]["prompt"]  # the tracked file is untouched
