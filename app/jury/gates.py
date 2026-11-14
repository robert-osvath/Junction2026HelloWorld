"""T5: Tier 0 deterministic mandate and rule gates (pure functions, zero AI)."""
from datetime import datetime, timezone
import math

from contracts import CatalogItem, Mandate, Need, Offer, Verdict

DECISION_PRIORITY = {"approve": 0, "escalate": 1, "reject": 2}


def _merge_decisions(decisions: list[str]) -> str:
    if not decisions:
        return "approve"
    return max(decisions, key=lambda d: DECISION_PRIORITY[d])


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def check_budget(mandate: Mandate, total_eur: float) -> tuple[str, list[str]]:
    """Check order total against hard budget and auto-approve threshold."""
    if not math.isfinite(total_eur) or total_eur < 0:
        return "reject", [f"Invalid order total: {total_eur}"]
    if total_eur > mandate.budget_per_order_eur:
        return (
            "reject",
            [f"Total {total_eur:,.2f} EUR exceeds budget {mandate.budget_per_order_eur:,.2f} EUR"],
        )
    if total_eur > mandate.auto_approve_limit_eur:
        return (
            "escalate",
            [
                f"Total {total_eur:,.2f} EUR exceeds auto-approve limit "
                f"{mandate.auto_approve_limit_eur:,.2f} EUR (within budget {mandate.budget_per_order_eur:,.2f} EUR)"
            ],
        )
    return (
        "approve",
        [f"Within auto-approve limit ({total_eur:,.2f} <= {mandate.auto_approve_limit_eur:,.2f} EUR)"],
    )


def check_sales_block(offer: Offer) -> tuple[str, list[str]]:
    """Reject offers that are sales-blocked in Valio Aimo's ERP."""
    if offer.sales_block:
        return "reject", [f"Product {offer.product_id} is sales-blocked"]
    return "approve", ["Not sales-blocked"]


def check_deadline(need: Need, offer: Offer) -> tuple[str, list[str]]:
    """Verify availableDate <= deliver_by deadline."""
    if offer.available_date > need.deliver_by:
        return (
            "reject",
            [f"Available date {offer.available_date} is after deadline {need.deliver_by}"],
        )
    return "approve", [f"Available by {offer.available_date} (deadline {need.deliver_by})"]


def check_closing_time(offer: Offer, now: datetime | None = None) -> tuple[str, list[str]]:
    """Verify the order is placed before the delivery route's closingTime cut-off."""
    if offer.closing_time is None or now is None:
        return "approve", []
    if _to_utc(now) > _to_utc(offer.closing_time):
        return (
            "reject",
            [f"Order time {now.isoformat()} is past cut-off {offer.closing_time.isoformat()}"],
        )
    return "approve", [f"Before closing time {offer.closing_time.isoformat()}"]


def check_claims(mandate: Mandate, item: CatalogItem | None) -> tuple[str, list[str]]:
    """Verify all required nutritional / dietary claims are present on the product."""
    if not mandate.required_claims or item is None:
        return "approve", []
    present = set(item.claims)
    missing = [c for c in mandate.required_claims if c not in present]
    if missing:
        return "reject", [f"Missing required claim(s): {', '.join(missing)}"]
    return "approve", [f"Declares required claim(s): {', '.join(mandate.required_claims)}"]


def check_allergens(
    mandate: Mandate,
    offer: Offer,
    item: CatalogItem | None = None,
) -> tuple[str, list[str]]:
    """Check GS1 banned allergens.

    Rules (from T5 spec):
    - Missing allergen data under any allergen rule -> escalate
    - Any banned allergen in allergens_contains -> reject
    - In strict_allergens mode, any banned allergen in allergens_may_contain -> reject
    """
    has_allergen_rule = bool(mandate.banned_allergens) or mandate.strict_allergens
    allergen_missing = offer.evidence.allergens == "missing" or (
        item is not None and not item.allergen_data
    )

    if allergen_missing:
        if has_allergen_rule:
            return (
                "escalate",
                ["Missing allergen declaration while mandate enforces allergen rules"],
            )
        return "approve", ["Allergen declaration missing (no allergen restrictions on mandate)"]

    if not mandate.banned_allergens or item is None:
        return "approve", []

    banned = set(mandate.banned_allergens)
    contains_hit = [a for a in item.allergens_contains if a in banned]
    if contains_hit:
        return "reject", [f"Contains banned allergen(s): {', '.join(contains_hit)}"]

    if mandate.strict_allergens:
        may_hit = [a for a in item.allergens_may_contain if a in banned]
        if may_hit:
            return (
                "reject",
                [f"Strict allergen mode: may contain banned allergen(s): {', '.join(may_hit)}"],
            )

    return "approve", [f"Cleared against banned allergens ({', '.join(mandate.banned_allergens)})"]


def check_stock_evidence(offer: Offer) -> tuple[str, list[str]]:
    """Check stock evidence and arithmetic consistency."""
    if (
        offer.units_needed <= 0
        or not math.isfinite(offer.unit_price_eur)
        or not math.isfinite(offer.total_eur)
        or offer.unit_price_eur < 0
        or offer.total_eur < 0
    ):
        return "reject", [f"Invalid line units or price on product {offer.product_id}"]
    if abs(offer.units_needed * offer.unit_price_eur - offer.total_eur) > 0.05:
        return (
            "reject",
            [
                f"Line total mismatch: {offer.units_needed} * {offer.unit_price_eur:.2f} "
                f"!= {offer.total_eur:.2f}"
            ],
        )
    if offer.evidence.stock == "none":
        return "reject", [f"Product {offer.product_id} has no stock available"]
    return "approve", []


def evaluate_tier0(
    mandate: Mandate,
    need: Need,
    offers: list[Offer],
    catalog_items: dict[str, CatalogItem] | None = None,
    now: datetime | None = None,
) -> Verdict:
    """Run all deterministic Tier 0 pure-code gates across the offered lines."""
    if not offers:
        return Verdict(
            juror="tier0",
            decision="reject",
            confidence=1.0,
            reasons=["No offers provided to evaluate"],
            model=None,
        )

    decisions: list[str] = []
    reasons: list[str] = []

    total_eur = round(sum(o.total_eur for o in offers), 2)
    b_dec, b_reasons = check_budget(mandate, total_eur)
    decisions.append(b_dec)
    reasons.extend(b_reasons)

    for offer in offers:
        item = (catalog_items or {}).get(offer.product_id)
        for dec, r_list in (
            check_sales_block(offer),
            check_stock_evidence(offer),
            check_deadline(need, offer),
            check_closing_time(offer, now=now),
            check_claims(mandate, item),
            check_allergens(mandate, offer, item),
        ):
            decisions.append(dec)
            reasons.extend(r_list)

    final_decision = _merge_decisions(decisions)
    # Keep reasons concise: if rejected or escalated, prioritize non-approve reasons first
    if final_decision != "approve":
        important = [
            r
            for r in reasons
            if not r.startswith(("Within auto-approve", "Not sales-blocked", "Available by"))
        ]
        if important:
            reasons = important

    return Verdict(
        juror="tier0",
        decision=final_decision,
        confidence=1.0,
        reasons=reasons,
        model=None,
    )
