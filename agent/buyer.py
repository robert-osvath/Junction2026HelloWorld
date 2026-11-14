"""MCP-backed buyer-agent workflow for the Junction P3 demo."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol

from contracts import Mandate, Need, Offer, OffersRequest, Order, OrderRequest


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


def parse_need(request: str, *, today: date | None = None) -> Need:
    """Parse the concise natural-language order requests used in the demo."""
    match = re.search(r"(?P<quantity>[\d,]+(?:\.\d+)?)\s*(?P<unit>L|kg|pcs)\b", request, re.IGNORECASE)
    if not match:
        raise ValueError("Request must include a quantity and unit, for example '1,000 L milk'.")
    quantity = float(match.group("quantity").replace(",", ""))
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


_STOCK_RANK = {"verified": 0, "estimated": 1, "none": 2}


def select_offer(offers: list[Offer], budget_eur: float | None = None) -> Offer:
    """Pick the one offer to order. Only offers within the mandate budget are considered (all of them
    if none fit, so the jury can still explain the rejection); among those: orderable (not sales-blocked)
    first, then the strongest stock evidence (verified > estimated > none), then the lowest total.
    Ties keep search order."""
    if not offers:
        raise ValueError("no offers to choose from")
    affordable = [o for o in offers if budget_eur is None or o.total_eur <= budget_eur] or offers
    return min(affordable, key=lambda o: (o.sales_block, _STOCK_RANK.get(o.evidence.stock, 3), o.total_eur))


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
        # search with the mandate's hard rules so the shortlist only holds candidates the jury can accept
        products = await self._tools.call(
            "search_products", query=need.product_intent, limit=10, quantity=need.quantity, unit=need.unit,
            claims=",".join(mandate.required_claims), exclude_allergens=",".join(mandate.banned_allergens),
        )
        product_ids = [p["product_id"] for p in products if p.get("product_id")]
        if not product_ids:
            return BuyerResult("rejected", f"No catalog products match '{need.product_intent}'.", need)
        offers_request = OffersRequest(need=need, product_ids=product_ids, delivery_date=need.deliver_by)
        raw_offers = await self._tools.call("get_offers", request=offers_request)
        offers = [Offer.model_validate(offer) for offer in raw_offers]
        if not offers:
            return BuyerResult("rejected", "No deliverable offers were returned.", need)
        # /offers returns ALTERNATIVES for one need (each already covers the full quantity), not order
        # lines: ordering them all would buy the quantity several times over and blow the budget.
        chosen = select_offer(offers, budget_eur=mandate.budget_per_order_eur)
        # The phrase is an explicit demo condition, not a fabricated stock assertion:
        # the server's Tier 0 juror produces the substitute path from this evidence.
        if "out of stock" in request.lower():
            chosen = chosen.model_copy(update={"sales_block": True,
                "evidence": chosen.evidence.model_copy(update={"stock": "none"})})
        order_request = OrderRequest(mandate_id=mandate.id, need=need, lines=[chosen])
        raw_order = await self._tools.call("place_order", request=order_request)
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
                           order_id=order.id, offers=tuple(offers), order=order)
