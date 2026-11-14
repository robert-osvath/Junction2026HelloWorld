import json
from pathlib import Path


CONFIG = Path(__file__).resolve().parents[1] / "agent_configs" / "vimo-live.json"


def test_vimo_waits_for_a_real_buyer_request_without_timeout_filler():
    config = json.loads(CONFIG.read_text())
    turn = config["conversation_config"]["turn"]
    prompt = config["conversation_config"]["agent"]["prompt"]["prompt"]

    assert turn["turn_timeout"] == -1
    assert turn["turn_eagerness"] == "normal"
    assert turn["speculative_turn"] is False
    assert "Never say “are you still there”" in prompt


def test_vimo_keeps_the_live_order_and_decision_tools():
    config = json.loads(CONFIG.read_text())
    tools = config["conversation_config"]["agent"]["prompt"]["tools"]
    names = {tool["name"] for tool in tools}

    assert {"get_backend_state", "create_order_draft", "execute_order_decision"} <= names
