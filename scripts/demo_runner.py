"""Run P3's three buyer-agent scenarios against one clean local API process.

Start the API separately (``python -m uvicorn app.main:app``), then run
``python scripts/demo_runner.py``.  Set ``AIMO_API_URL`` for a deployed API.
"""
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import date
from typing import Any, Awaitable, Callable, Protocol

import httpx

# `python scripts/demo_runner.py` puts scripts/ on sys.path, not the repo root: make `agent` importable
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

HAPPY_REQUEST = "1,000 L lactose-free milk by Monday"
STOCK_OUT_REQUEST = "1,000 L lactose-free milk by Monday; first choice is out of stock"


class BuyerAgent(Protocol):
    async def run(self, request: str, profile_key: str = "hotel", *, today: date | None = None) -> Any: ...


ApproveOrder = Callable[[str], Awaitable[dict[str, Any]]]
VerifyAudit = Callable[[], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class DemoOutcome:
    scenario: str
    status: str
    order_id: str


def _field(result: Any, name: str) -> Any:
    return result.get(name) if isinstance(result, dict) else getattr(result, name, None)


def _substitutes(result: Any) -> list[dict[str, Any]]:
    direct = _field(result, "substitutes")
    if direct is not None:
        return direct
    order = _field(result, "order")
    return (order.get("substitutes", []) if isinstance(order, dict) else getattr(order, "substitutes", [])) or []


async def run_demo(agent: BuyerAgent, approve_order: ApproveOrder, verify_audit: VerifyAudit) -> list[DemoOutcome]:
    """Exercise auto-order, substitute+approval, and budget+approval paths."""
    today = date(2026, 11, 14)
    happy = await agent.run(HAPPY_REQUEST, profile_key="hotel", today=today)
    assert _field(happy, "status") == "placed", "happy path must auto-order"

    stock_out = await agent.run(STOCK_OUT_REQUEST, profile_key="hotel", today=today)
    assert _field(stock_out, "status") == "substitutes_proposed", "stock-out must enter substitute review"
    assert _substitutes(stock_out), "stock-out path must return ranked substitutes"
    # P4 permits human rejection of a substitute proposal but does not yet permit
    # approving one; preserve the jury's result and make the escalation visible.

    over_budget = await agent.run(HAPPY_REQUEST, profile_key="hotel_tight", today=today)
    assert _field(over_budget, "status") == "pending_approval", "tight mandate must hold for a human"
    approved = await approve_order(_field(over_budget, "order_id"))
    assert approved["status"] == "placed", "human approval must place the order"

    audit = await verify_audit()
    assert audit["ok"] is True, "audit chain must verify"
    return [
        DemoOutcome("happy", _field(happy, "status"), _field(happy, "order_id")),
        DemoOutcome("stock-out", _field(stock_out, "status"), _field(stock_out, "order_id")),
        DemoOutcome("over-budget", approved["status"], _field(over_budget, "order_id")),
    ]


def _http_callbacks(base_url: str) -> tuple[ApproveOrder, VerifyAudit]:
    async def approve(order_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
            response = await client.post(f"/orders/{order_id}/approve")
            response.raise_for_status()
            return response.json()

    async def verify() -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
            response = await client.get("/audit/verify")
            response.raise_for_status()
            return response.json()

    return approve, verify


async def _start_clean_local_api() -> tuple[str, asyncio.subprocess.Process]:
    """Start a disposable in-memory-order API so the demo needs one command."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "DB_URL": ""}
    port = "8765"
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", port,
        cwd=root, env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    base_url = f"http://127.0.0.1:{port}"
    async with httpx.AsyncClient(timeout=1.0) as client:
        for _ in range(50):
            if process.returncode is not None:
                error = (await process.stderr.read()).decode(errors="replace") if process.stderr else ""
                raise RuntimeError(f"Local API exited during startup: {error}")
            try:
                if (await client.get(f"{base_url}/health")).is_success:
                    return base_url, process
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    process.terminate()
    await process.wait()
    raise RuntimeError("Local API did not become healthy within 5 seconds")


async def main() -> None:
    from agent.buyer import BuyerAgent as MCPBuyerAgent
    from agent.mcp_tools import StdioMCPTools

    base_url = os.getenv("AIMO_API_URL")
    process = None
    if base_url is None:
        base_url, process = await _start_clean_local_api()
    try:
        approve, verify = _http_callbacks(base_url)
        async with StdioMCPTools(base_url=base_url) as tools:
            outcomes = await run_demo(MCPBuyerAgent(tools), approve, verify)
        for outcome in outcomes:
            print(f"{outcome.scenario}: {outcome.order_id} {outcome.status}")
        # T11 spec: the over-budget scenario ends with the audit log shown
        over_budget = next(o for o in outcomes if o.scenario == "over-budget")
        async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
            response = await client.get("/audit", params={"order_id": over_budget.order_id})
            response.raise_for_status()
        print(f"audit log for {over_budget.order_id}:")
        for event in response.json():
            print(f"  {event['ts']}  {event['actor']:<12} {event['action']:<20} {event['human_summary']}")
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            await process.wait()


if __name__ == "__main__":
    asyncio.run(main())
