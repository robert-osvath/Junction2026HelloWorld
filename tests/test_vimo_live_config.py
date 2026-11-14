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


def test_publish_script_only_updates_vimo_turn_settings() -> None:
    patch = publish_module().turn_patch(live_config())

    assert patch == {
        "conversation_config": {
            "agent": {"prompt": {"ignore_default_personality": True}},
            "turn": {"turn_timeout": -1, "turn_eagerness": "normal", "speculative_turn": False},
        },
    }


def test_publish_script_refuses_a_non_live_branch() -> None:
    with pytest.raises(ValueError, match="100% live traffic"):
        publish_module().require_fully_live({"current_live_percentage": 1})

    publish_module().require_fully_live({"current_live_percentage": 100})
