"""MCP-backed buyer-agent workflow for the Junction P3 demo."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol

from contracts import Mandate, Need, Offer, Order


class MCPTools(Protocol):
    async def call(self, name: str, **arguments: Any) -> Any: ...


@dataclass(frozen=True)
class BuyerProfile:
    key: str
    mandate: Mandate


BUYER_PROFILES = {
    "hotel": BuyerProfile("hotel", Mandate(
        id="mandate-hotel-001", buyer_name="Hotel Kalevala Helsinki", buyer_type="hotel",
        budget_per_order_eur=2500, auto_approve_limit_eur=2000,
        required_claims=["FREE_FROM_LACTOSE"],
    )),
    "hotel_tight": BuyerProfile("hotel_tight", Mandate(
        id="mandate-hotel-tight", buyer_name="Hotel Kalevala Helsinki", buyer_type="hotel",
        budget_per_order_eur=2500, auto_approve_limit_eur=1000,
        required_claims=["FREE_FROM_LACTOSE"],
    )),
    "hospital": BuyerProfile("hospital", Mandate(
        id="mandate-hospital-001", buyer_name="Helsinki Central Hospital", buyer_type="hospital",
        budget_per_order_eur=5000, auto_approve_limit_eur=1000,
        banned_allergens=["AW", "AE", "AN"], strict_allergens=True,
    )),
}


@dataclass(frozen=True)
class BuyerResult:
    status: str
    message: str
    need: Need
    order_id: str | None = None
    offers: tuple[Offer, ...] = ()
    order: Order | None = None


def _parse_quantity(text: str) -> float:
    """"1,000" / "1,000.5" use commas as thousands separators; any other comma is a decimal comma
    (Finnish style: "1,5" = 1.5, not 15)."""
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", text):
        return float(text.replace(",", ""))
    if text.count(",") + text.count(".") > 1:
        raise ValueError(f"Ambiguous quantity '{text}'; write it like '1000', '1,000' or '1,5'.")
    return float(text.replace(",", "."))


def parse_need(request: str, *, today: date | None = None) -> Need:
    """Parse the concise natural-language order requests used in the demo."""
    match = re.search(r"(?P<quantity>\d+(?:[.,]\d+)*)\s*(?P<unit>L|kg|pcs)\b", request, re.IGNORECASE)
    if not match:
        raise ValueError("Request must include a quantity and unit, for example '1,000 L milk'.")
    quantity = _parse_quantity(match.group("quantity"))
    raw_unit = match.group("unit")
    unit = "L" if raw_unit.lower() == "l" else "kg" if raw_unit.lower() == "kg" else "pcs"
    reference = today or date.today()
    weekday = re.search(r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b", request, re.IGNORECASE)
    if weekday:
        names = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
        target = names.index(weekday.group(1).lower())
        deliver_by = reference + timedelta(days=(target - reference.weekday()) % 7 or 7)
    else:
        iso_date = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", request)
        deliver_by = date.fromisoformat(iso_date.group(1)) if iso_date else reference + timedelta(days=1)
    intent = request[match.end():]
    intent = re.sub(r"\b(by|for)\s+(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|\d{4}-\d{2}-\d{2}).*", "", intent, flags=re.IGNORECASE).strip(" ,.;")
    intent = re.sub(r";\s*first choice is out of stock.*", "", intent, flags=re.IGNORECASE).strip()
    return Need(text=request, product_intent=intent or "products", quantity=quantity, unit=unit, deliver_by=deliver_by)


class BuyerAgent:
    def __init__(self, tools: MCPTools, profiles: dict[str, BuyerProfile] | None = None) -> None:
        self._tools = tools
        self._profiles = profiles or BUYER_PROFILES

    async def run(self, request: str, profile_key: str = "hotel", *, today: date | None = None) -> BuyerResult:
        if profile_key not in self._profiles:
            raise ValueError(f"Unknown buyer profile: {profile_key}")
        profile = self._profiles[profile_key]
        need = parse_need(request, today=today)
        mandate = profile.mandate
        # The buyer only states the need; the backend searches, prices, lets the jury compare the
        # alternatives and picks the best one. "out of stock" is an explicit demo condition: the server
        # blocks every candidate so the substitute path runs.
        raw_order = await self._tools.call(
            "order_from_need", need=need, mandate_id=mandate.id,
            simulate_out_of_stock="out of stock" in request.lower())
        order = Order.model_validate(raw_order)
        status = order.status
        confirmation = f" (confirmation {order.confirmation_no})" if order.confirmation_no else ""
        messages = {
            "placed": f"Order {order.id} was placed{confirmation}.",
            "pending_approval": f"Order {order.id} is waiting for human approval.",
            "substitutes_proposed": f"Order {order.id} needs substitutes; review the proposed alternatives.",
            "rejected": f"Order {order.id} was rejected.",
        }
        return BuyerResult(status, messages.get(status, f"Order {order.id} returned status {status}."), need,
                           order_id=order.id, offers=tuple(order.lines), order=order)
