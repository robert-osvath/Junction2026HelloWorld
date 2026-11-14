"""Run P3's three buyer-agent scenarios against one clean local API process.

Start the API separately (``python -m uvicorn app.main:app``), then run
``python scripts/demo_runner.py``.  Set ``AIMO_API_URL`` for a deployed API.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Awaitable, Callable, Protocol

import httpx


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
    return (_field(result, "order") or {}).get("substitutes", [])


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


async def main() -> None:
    from agent.buyer import BuyerAgent as MCPBuyerAgent
    from agent.mcp_tools import LocalMCPTools

    approve, verify = _http_callbacks(os.getenv("AIMO_API_URL", "http://127.0.0.1:8000"))
    outcomes = await run_demo(MCPBuyerAgent(LocalMCPTools()), approve, verify)
    for outcome in outcomes:
        print(f"{outcome.scenario}: {outcome.order_id} {outcome.status}")


if __name__ == "__main__":
    asyncio.run(main())
