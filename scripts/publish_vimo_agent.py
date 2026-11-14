"""Publish the tracked Vimo turn settings to its ElevenLabs agent branch.

Run without ``--apply`` to inspect the exact target. Publishing requires
``ELEVENLABS_API_KEY`` and is deliberately an explicit, manual release step.
"""

import argparse
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv


ROOT = Path(__file__).parents[1]
CONFIG_PATH = ROOT / "agent_configs" / "vimo-live.json"
AGENTS_PATH = ROOT / "agents.json"
API_BASE = "https://api.elevenlabs.io/v1/convai/agents"


def deployment_target() -> tuple[dict, dict]:
    config = json.loads(CONFIG_PATH.read_text())
    registered = json.loads(AGENTS_PATH.read_text())["agents"]
    agent = next((item for item in registered if item["config"] == "agent_configs/vimo-live.json"), None)
    if agent is None:
        raise ValueError("Vimo is missing from agents.json")
    return config, agent


def turn_patch(config: dict) -> dict:
    """Return only the settings owned by this release, never a full overwrite."""
    prompt = config["conversation_config"]["agent"]["prompt"]
    turn = config["conversation_config"]["turn"]
    return {
        "conversation_config": {
            "agent": {"prompt": {"ignore_default_personality": prompt["ignore_default_personality"]}},
            "turn": {
                "turn_timeout": turn["turn_timeout"],
                "turn_eagerness": turn["turn_eagerness"],
                "speculative_turn": turn["speculative_turn"],
            },
        },
    }


def require_fully_live(branch: dict) -> None:
    """Refuse to update a staging branch that would not receive conversations."""
    percentage = float(branch.get("current_live_percentage", 0))
    if not 99.9 <= percentage <= 100.1:
        raise ValueError(
            "Refusing to publish to a branch without 100% live traffic. "
            "Deploy or merge the intended branch in ElevenLabs first."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="PATCH the configured ElevenLabs agent branch")
    args = parser.parse_args()

    config, agent = deployment_target()
    patch = turn_patch(config)
    agent_id = agent["id"]
    branch_id = agent["branch_id"]
    if not args.apply:
        print(f"Dry run: would update ElevenLabs agent {agent_id} branch {branch_id}")
        print(json.dumps(patch, indent=2, sort_keys=True))
        print("Run with --apply after review to publish these settings.")
        return

    load_dotenv(ROOT / ".env")
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise SystemExit("ELEVENLABS_API_KEY is required to publish Vimo settings")
    headers = {"xi-api-key": api_key}
    branch = httpx.get(f"{API_BASE}/{agent_id}/branches/{branch_id}", headers=headers, timeout=30)
    branch.raise_for_status()
    require_fully_live(branch.json())
    response = httpx.patch(
        f"{API_BASE}/{agent_id}",
        params={"branch_id": branch_id}, headers=headers,
        json=patch,
        timeout=30,
    )
    response.raise_for_status()
    print(f"Published Vimo turn settings to agent {agent_id} branch {branch_id}")


if __name__ == "__main__":
    main()
