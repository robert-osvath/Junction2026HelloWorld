from __future__ import annotations

from datetime import datetime

import pytest

from app.jury.gates import (
    check_allergens,
    check_budget,
    check_claims,
    check_closing_time,
    check_deadline,
    check_sales_block,
    check_stock_evidence,
    evaluate_tier0,
)
from contracts.models import CatalogItem, Mandate, Need, Offer


def _mandate(**overrides) -> Mandate:
    base = {
        "id": "mandate-test-001",
        "buyer_name": "Hotel Test Helsinki",
        "buyer_type": "hotel",
        "budget_per_order_eur": 2500.0,
        "auto_approve_limit_eur": 1000.0,
        "required_claims": ["FREE_FROM_LACTOSE"],
        "banned_allergens": ["AP", "AN"],
        "strict_allergens": False,
        "substitutes_allowed": True,
    }
    base.update(overrides)
    return Mandate.model_validate(base)


def _need(**overrides) -> Need:
    base = {
        "text": "200 L lactose-free milk by Monday",
        "product_intent": "lactose-free milk",
        "quantity": 200,
        "unit": "L",
        "deliver_by": "2026-11-16",
        "extra_constraints": [],
    }
    base.update(overrides)
    return Need.model_validate(base)


def _offer(**overrides) -> Offer:
    base = {
        "product_id": "463038",
        "name": "Valio Eila® semi skimmed milk drink 10 l novobox lactose free",
        "sales_unit": "KI",
        "units_needed": 20,
        "unit_price_eur": 40.0,
        "comparison_price_eur": 2.0,
        "comparison_unit": "L",
        "total_eur": 800.0,
        "available_date": "2026-11-15",
        "closing_time": "2026-11-14T16:00:00+00:00",
        "sales_block": False,
        "logistic_model": "Stock",
        "evidence": {
            "stock": "verified",
            "allergens": "declared",
            "reliability": 0.99,
        },
    }
    base.update(overrides)
    return Offer.model_validate(base)


def _catalog(**overrides) -> CatalogItem:
    base = {
        "product_id": "463038",
        "name": "Valio Eila® semi skimmed milk drink 10 l novobox lactose free",
        "vendor_name": "VALIO OY TUORE",
        "sales_unit": "KI",
        "category": "10400",
        "claims": ["FREE_FROM_LACTOSE", "VEGETARIAN"],
        "allergens_contains": ["AM"],
        "allergens_may_contain": [],
        "allergen_data": True,
    }
    base.update(overrides)
    return CatalogItem.model_validate(base)


@pytest.mark.parametrize(
    "case_id,mandate_kw,need_kw,offer_kw,catalog_kw,now_iso,expected_decision,expected_reason_substr",
    [
        # 1. Happy path within auto-approve limit -> approve
        (
            "G01_within_auto_approve",
            {},
            {},
            {"total_eur": 800.0},
            {},
            "2026-11-14T10:00:00+00:00",
            "approve",
            "Within auto-approve limit",
        ),
        # 2. Exact auto-approve boundary -> approve
        (
            "G02_exact_auto_approve",
            {},
            {},
            {"units_needed": 25, "unit_price_eur": 40.0, "total_eur": 1000.0},
            {},
            "2026-11-14T10:00:00+00:00",
            "approve",
            "Within auto-approve limit",
        ),
        # 3. Between auto-approve limit and budget cap -> escalate
        (
            "G03_over_auto_approve_under_budget",
            {},
            {},
            {"units_needed": 40, "unit_price_eur": 40.0, "total_eur": 1600.0},
            {},
            "2026-11-14T10:00:00+00:00",
            "escalate",
            "exceeds auto-approve limit",
        ),
        # 4. Over hard budget cap -> reject
        (
            "G04_over_budget_cap",
            {},
            {},
            {"units_needed": 70, "unit_price_eur": 40.0, "total_eur": 2800.0},
            {},
            "2026-11-14T10:00:00+00:00",
            "reject",
            "exceeds budget",
        ),
        # 5. Line arithmetic mismatch -> reject
        (
            "G05_line_math_mismatch",
            {},
            {},
            {"units_needed": 20, "unit_price_eur": 40.0, "total_eur": 850.0},
            {},
            "2026-11-14T10:00:00+00:00",
            "reject",
            "Line total mismatch",
        ),
        # 6. Sales block active on offer -> reject
        (
            "G06_sales_block_true",
            {},
            {},
            {"sales_block": True},
            {},
            "2026-11-14T10:00:00+00:00",
            "reject",
            "sales-blocked",
        ),
        # 7. Available date after deliver_by deadline -> reject
        (
            "G07_late_available_date",
            {},
            {"deliver_by": "2026-11-16"},
            {"available_date": "2026-11-18"},
            {},
            "2026-11-14T10:00:00+00:00",
            "reject",
            "after deadline",
        ),
        # 8. Available date equals deliver_by deadline -> approve
        (
            "G08_same_day_deadline",
            {},
            {"deliver_by": "2026-11-16"},
            {"available_date": "2026-11-16"},
            {},
            "2026-11-14T10:00:00+00:00",
            "approve",
            "Available by",
        ),
        # 9. Order placed after closing_time cutoff -> reject
        (
            "G09_past_closing_time",
            {},
            {},
            {"closing_time": "2026-11-14T08:00:00+00:00"},
            {},
            "2026-11-14T10:00:00+00:00",
            "reject",
            "past cut-off",
        ),
        # 10. No closing_time cutoff set -> approve
        (
            "G10_no_closing_time",
            {},
            {},
            {"closing_time": None},
            {},
            "2026-11-14T10:00:00+00:00",
            "approve",
            "Within auto-approve limit",
        ),
        # 11. Stock evidence is 'none' -> reject
        (
            "G11_stock_none",
            {},
            {},
            {"evidence": {"stock": "none", "allergens": "declared", "reliability": 0.99}},
            {},
            "2026-11-14T10:00:00+00:00",
            "reject",
            "no stock available",
        ),
        # 12. Missing required claim -> reject
        (
            "G12_missing_required_claim",
            {"required_claims": ["FREE_FROM_LACTOSE", "ORGANIC"]},
            {},
            {},
            {"claims": ["FREE_FROM_LACTOSE"]},
            "2026-11-14T10:00:00+00:00",
            "reject",
            "Missing required claim",
        ),
        # 13. Banned GS1 allergen in allergens_contains -> reject
        (
            "G13_banned_allergen_contains",
            {"banned_allergens": ["AM"]},
            {},
            {},
            {"allergens_contains": ["AM"]},
            "2026-11-14T10:00:00+00:00",
            "reject",
            "Contains banned allergen",
        ),
        # 14. Banned GS1 allergen in allergens_may_contain with strict_allergens=False -> approve
        (
            "G14_may_contain_non_strict",
            {"banned_allergens": ["AN"], "strict_allergens": False},
            {},
            {},
            {"allergens_contains": ["AM"], "allergens_may_contain": ["AN"]},
            "2026-11-14T10:00:00+00:00",
            "approve",
            "Cleared against banned allergens",
        ),
        # 15. Banned GS1 allergen in allergens_may_contain with strict_allergens=True -> reject
        (
            "G15_may_contain_strict",
            {"banned_allergens": ["AN"], "strict_allergens": True},
            {},
            {},
            {"allergens_contains": ["AM"], "allergens_may_contain": ["AN"]},
            "2026-11-14T10:00:00+00:00",
            "reject",
            "Strict allergen mode: may contain banned allergen",
        ),
        # 16. Missing allergen data when allergen rule active (evidence='missing') -> escalate
        (
            "G16_missing_allergen_evidence_with_rule",
            {"banned_allergens": ["AP"]},
            {},
            {"evidence": {"stock": "verified", "allergens": "missing", "reliability": 0.99}},
            {"allergen_data": False},
            "2026-11-14T10:00:00+00:00",
            "escalate",
            "Missing allergen declaration",
        ),
        # 17. Missing allergen data when NO allergen rule active -> approve
        (
            "G17_missing_allergen_evidence_no_rule",
            {"banned_allergens": [], "strict_allergens": False},
            {},
            {"evidence": {"stock": "verified", "allergens": "missing", "reliability": 0.99}},
            {"allergen_data": False},
            "2026-11-14T10:00:00+00:00",
            "approve",
            "no allergen restrictions",
        ),
        # 18. Empty offers list -> reject
        (
            "G18_empty_offers_list",
            {},
            {},
            {},
            {},
            "2026-11-14T10:00:00+00:00",
            "reject",
            "No offers provided",
        ),
    ],
)
def test_tier0_gates_table_driven(
    case_id: str,
    mandate_kw: dict,
    need_kw: dict,
    offer_kw: dict,
    catalog_kw: dict,
    now_iso: str,
    expected_decision: str,
    expected_reason_substr: str,
) -> None:
    mandate = _mandate(**mandate_kw)
    need = _need(**need_kw)
    offer = _offer(**offer_kw)
    catalog = _catalog(**catalog_kw)
    now_dt = datetime.fromisoformat(now_iso)

    offers_list = [] if case_id == "G18_empty_offers_list" else [offer]
    verdict = evaluate_tier0(
        mandate,
        need,
        offers_list,
        catalog_items={catalog.product_id: catalog},
        now=now_dt,
    )
    assert verdict.juror == "tier0"
    assert verdict.decision == expected_decision, f"{case_id}: got {verdict.decision} ({verdict.reasons})"
    joined_reasons = " | ".join(verdict.reasons)
    assert expected_reason_substr in joined_reasons, (
        f"{case_id}: expected substring '{expected_reason_substr}' in '{joined_reasons}'"
    )


def test_adversarial_naive_vs_aware_closing_time() -> None:
    """Comparing naive closing_time with timezone-aware now (or vice versa) must not raise TypeError."""
    offer_naive = _offer(closing_time="2026-11-14T08:00:00")
    dec, reasons = check_closing_time(offer_naive, now=datetime.fromisoformat("2026-11-14T10:00:00+00:00"))
    assert dec == "reject"
    assert "past cut-off" in " ".join(reasons)


def test_adversarial_multiline_order_cost_aggregation() -> None:
    """Two lines of 800 EUR each (each <= 1,000 EUR auto-approve) total 1,600 EUR -> must escalate both tier0 and cost."""
    from app.jury.jurors import evaluate_with_gemini_decision

    mandate = _mandate(auto_approve_limit_eur=1000.0, budget_per_order_eur=2500.0)
    need = _need()
    line1 = _offer(product_id="463038", units_needed=20, unit_price_eur=40.0, total_eur=800.0)
    line2 = _offer(product_id="463010", units_needed=20, unit_price_eur=40.0, total_eur=800.0)
    cat1 = _catalog(product_id="463038")
    cat2 = _catalog(product_id="463010")

    metrics = evaluate_with_gemini_decision(
        mandate,
        need,
        [line1, line2],
        catalog_items={"463038": cat1, "463010": cat2},
        live_api=False,
    )
    assert metrics.result.decision == "human_approval"
    verdicts_by_juror = {v.juror: v for v in metrics.result.verdicts}
    assert verdicts_by_juror["tier0"].decision == "escalate"
    assert verdicts_by_juror["cost"].decision == "escalate"


def test_adversarial_empty_offers_in_jurors() -> None:
    """Empty offers list in evaluate_with_gemini_decision must not raise IndexError."""
    from app.jury.jurors import evaluate_with_gemini_decision

    metrics = evaluate_with_gemini_decision(_mandate(), _need(), [], live_api=False)
    assert metrics.result.decision == "reject"
    assert metrics.result.chosen_product_id is None

