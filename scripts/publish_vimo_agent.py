"""Publish the tracked Vimo prompt, client tools and turn settings to its ElevenLabs agent branch.

Run without ``--apply`` to inspect the exact target. Publishing requires
``ELEVENLABS_API_KEY`` and is deliberately an explicit, manual release step.
"""

import argparse
import copy
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv


ROOT = Path(__file__).parents[1]
CONFIG_PATH = ROOT / "agent_configs" / "vimo-live.json"
AGENTS_PATH = ROOT / "agents.json"
API_BASE = "https://api.elevenlabs.io/v1/convai/agents"
TOOLS_BASE = "https://api.elevenlabs.io/v1/convai/tools"


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
            "agent": {"prompt": {
                "ignore_default_personality": prompt["ignore_default_personality"],
                "prompt": prompt["prompt"],  # tools are attached by id (tool_ids), see plan_tools
                "max_tokens": prompt["max_tokens"],  # a plan_event call for a full menu needs a few hundred tokens
            }},
            "turn": {
                "turn_timeout": turn["turn_timeout"],
                "turn_eagerness": turn["turn_eagerness"],
                "speculative_turn": turn["speculative_turn"],
            },
        },
    }


def plan_tools(tracked: list[dict], live: dict[str, tuple[str, dict]]) -> list[tuple[str, dict, str | None]]:
    """(action, tool, live_id) per tracked tool: keep, update or create. `live` maps name -> (id, config).
    Only description and parameters are compared; ElevenLabs fills in its own defaults for the rest."""
    plan = []
    for tool in tracked:
        found = live.get(tool["name"])
        if found is None:
            plan.append(("create", tool, None))
        else:
            tool_id, cfg = found
            same = cfg.get("description") == tool["description"] and _norm(cfg.get("parameters")) == _norm(tool.get("parameters"))
            plan.append(("keep" if same else "update", tool, tool_id))
    return plan


def _norm(x):
    """Drop empty values and platform-filled keys so a live schema compares equal to the tracked one."""
    if isinstance(x, dict):
        return {k: _norm(v) for k, v in x.items()
                if v not in (None, "", [], {}, False) and k not in ("dynamic_variable", "constant_value", "is_system_provided", "value_type")}
    if isinstance(x, list):
        return [_norm(v) for v in x]
    return x


def live_tools(headers: dict, tool_ids: list[str]) -> dict[str, tuple[str, dict]]:
    """Tools attached to the agent, plus same-named workspace tools left unattached by an interrupted
    publish (reused instead of creating duplicates)."""
    tools = {}
    listing = httpx.get(TOOLS_BASE, headers=headers, timeout=30)
    listing.raise_for_status()
    for item in listing.json().get("tools", []):
        cfg = item.get("tool_config") or {}
        if cfg.get("name"):
            tools.setdefault(cfg["name"], (item["id"], cfg))
    for tool_id in tool_ids:
        r = httpx.get(f"{TOOLS_BASE}/{tool_id}", headers=headers, timeout=30)
        r.raise_for_status()
        cfg = r.json()["tool_config"]
        tools[cfg["name"]] = (tool_id, cfg)
    return tools


def sync_tools(headers: dict, tool_plan: list[tuple[str, dict, str | None]]) -> list[str]:
    """Apply a plan_tools plan; returns the tool ids in tracked order."""
    tool_ids = []
    for action, tool, tool_id in tool_plan:
        if action == "create":
            r = httpx.post(TOOLS_BASE, headers=headers, json={"tool_config": tool}, timeout=30)
            r.raise_for_status()
            tool_id = r.json()["id"]
        elif action == "update":
            httpx.patch(f"{TOOLS_BASE}/{tool_id}", headers=headers, json={"tool_config": tool}, timeout=30).raise_for_status()
        print(f"  tool {tool['name']}: {action} ({tool_id})")
        tool_ids.append(tool_id)
    return tool_ids


def create_body(config: dict, tool_ids: list[str]) -> dict:
    """The full tracked agent for a new workspace: tools attached by their new ids, not inline."""
    conversation = copy.deepcopy(config["conversation_config"])
    prompt = conversation["agent"]["prompt"]
    prompt.pop("tools", None)
    prompt["tool_ids"] = tool_ids
    return {"name": config["name"], "tags": config.get("tags", []), "conversation_config": conversation,
            "platform_settings": config.get("platform_settings", {})}


def create_agent(config: dict, headers: dict) -> dict:
    """Create Vimo in the workspace of ELEVENLABS_API_KEY (rerunnable: reuses tools and the agent by name)
    and point agents.json at it."""
    existing = httpx.get(API_BASE, params={"search": config["name"], "page_size": 100}, headers=headers, timeout=30)
    existing.raise_for_status()
    match = [a for a in existing.json().get("agents", []) if a.get("name") == config["name"]]
    if match:
        agent_id = match[0]["agent_id"]
        print(f"Agent {config['name']} already exists: {agent_id}")
    else:
        tool_ids = sync_tools(headers, plan_tools(config["conversation_config"]["agent"]["prompt"]["tools"],
                                                  live_tools(headers, [])))
        r = httpx.post(f"{API_BASE}/create", headers=headers, json=create_body(config, tool_ids), timeout=60)
        if r.status_code >= 400:
            raise SystemExit(f"create failed {r.status_code}: {r.text[:500]}")
        agent_id = r.json()["agent_id"]
        print(f"Created agent {config['name']}: {agent_id}")
    agent = httpx.get(f"{API_BASE}/{agent_id}", headers=headers, timeout=30)
    agent.raise_for_status()
    body = agent.json()
    registered = {"agents": [{"config": "agent_configs/vimo-live.json", "id": agent_id,
                              "branch_id": body.get("branch_id") or body.get("main_branch_id"),
                              "version_id": body.get("version_id")}]}
    AGENTS_PATH.write_text(json.dumps(registered, indent=4) + "\n")
    print(f"agents.json now points at {agent_id}")
    return body


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
    parser.add_argument("--apply", action="store_true", help="update tools and PATCH the configured ElevenLabs agent branch")
    parser.add_argument("--create", action="store_true",
                        help="create the agent and its tools in ELEVENLABS_API_KEY's workspace (account move)")
    args = parser.parse_args()

    if args.create:
        load_dotenv(ROOT / ".env")
        if not os.getenv("ELEVENLABS_API_KEY"):
            raise SystemExit("ELEVENLABS_API_KEY is required")
        create_agent(json.loads(CONFIG_PATH.read_text()), {"xi-api-key": os.environ["ELEVENLABS_API_KEY"]})
        return

    config, agent = deployment_target()
    patch = turn_patch(config)
    agent_id = agent["id"]
    branch_id = agent["branch_id"]
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise SystemExit("ELEVENLABS_API_KEY is required (also for the dry run, which reads the live tools)")
    headers = {"xi-api-key": api_key}
    branch = httpx.get(f"{API_BASE}/{agent_id}/branches/{branch_id}", headers=headers, timeout=30)
    branch.raise_for_status()
    require_fully_live(branch.json())
    live_agent = httpx.get(f"{API_BASE}/{agent_id}", params={"branch_id": branch_id}, headers=headers, timeout=30)
    live_agent.raise_for_status()
    live_ids = live_agent.json()["conversation_config"]["agent"]["prompt"].get("tool_ids") or []
    live = live_tools(headers, live_ids)
    tool_plan = plan_tools(config["conversation_config"]["agent"]["prompt"]["tools"], live)
    tracked = {t["name"] for _, t, _ in tool_plan}
    dropped = sorted(name for name, (tool_id, _) in live.items() if tool_id in live_ids and name not in tracked)

    if not args.apply:
        print(f"Dry run: would update ElevenLabs agent {agent_id} branch {branch_id}")
        for action, tool, tool_id in tool_plan:
            attached = "" if tool_id is None or tool_id in live_ids else ", unattached workspace tool"
            print(f"  tool {tool['name']}: {action}{f' ({tool_id}{attached})' if tool_id else ''}")
        if dropped:
            print(f"  tools detached (not in the tracked config): {', '.join(dropped)}")
        print(json.dumps(patch, indent=2, sort_keys=True))
        print("Run with --apply after review to publish these settings.")
        return

    tool_ids = sync_tools(headers, tool_plan)
    patch["conversation_config"]["agent"]["prompt"]["tool_ids"] = tool_ids
    response = httpx.patch(
        f"{API_BASE}/{agent_id}",
        params={"branch_id": branch_id}, headers=headers,
        json=patch,
        timeout=30,
    )
    response.raise_for_status()
    print(f"Published Vimo prompt, {len(tool_ids)} tools and turn settings to agent {agent_id} branch {branch_id}")


if __name__ == "__main__":
    main()
