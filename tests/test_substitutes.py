"""T7 done-when: Substitute ranker works for a product with an explicit rule and one without,
returns top-3 (Best/Better/Good) with one-line trade-offs, and screens substitutes back through the jury."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.jury.router import evaluate
from app.jury.substitutes import rank_substitutes, rank_substitutes_detailed
from app.main import app
from app.mocks import FIXTURES
from contracts import JuryRequest, Mandate, Need, Offer, SubstitutesRequest

client = TestClient(app)
MANDATE = Mandate.model_validate_json((FIXTURES / "mandate.json").read_text())
NEED = Need.model_validate_json((FIXTURES / "need.json").read_text())
OFFER = Offer.model_validate_json((FIXTURES / "offer.json").read_text())


def test_explicit_substitution_rule_returns_top_3_good_better_best() -> None:
    # Product 463038 has explicit substitution rules in EXPLICIT_SUBSTITUTIONS
    req = SubstitutesRequest(
        mandate=MANDATE,
        need=NEED,
        offer=OFFER.model_copy(update={"sales_block": True}),
    )
    ranked = rank_substitutes_detailed(req)
    assert len(ranked) == 3
    assert [r.tier for r in ranked] == ["Best", "Better", "Good"]
    assert any(r.is_explicit_rule for r in ranked)

    # Scores are strictly non-increasing
    assert ranked[0].score >= ranked[1].score >= ranked[2].score

    # Every returned Offer has a one-line trade-off tag and passes the Jury
    for r in ranked:
        assert f"[{r.tier}:" in r.offer.name
        assert r.offer.product_id != OFFER.product_id
        assert r.jury_decision in ("auto_order", "human_approval")

        jury_res = evaluate(JuryRequest(mandate=MANDATE, need=NEED, offers=[r.offer]))
        assert jury_res.decision in ("auto_order", "human_approval")


def test_same_category_fallback_without_explicit_rule() -> None:
    # Product 400101 (regular lactose-containing milk) has no explicit rule in EXPLICIT_SUBSTITUTIONS
    non_lactose_free_offer = OFFER.model_copy(
        update={
            "product_id": "400101",
            "name": "Valio Kevytmaito semi-skimmed milk 10L novobox (contains lactose)",
            "unit_price_eur": 26.0,
            "comparison_price_eur": 1.30,
            "total_eur": 1300.0,
        }
    )
    req = SubstitutesRequest(mandate=MANDATE, need=NEED, offer=non_lactose_free_offer)
    ranked = rank_substitutes_detailed(req)

    assert len(ranked) == 3
    assert all(not r.is_explicit_rule for r in ranked)
    for r in ranked:
        assert r.offer.product_id != "400101"
        assert "same-category match" in r.tradeoff
        jury_res = evaluate(JuryRequest(mandate=MANDATE, need=NEED, offers=[r.offer]))
        assert jury_res.decision in ("auto_order", "human_approval")


def test_strict_allergens_filters_may_contain_candidates() -> None:
    # Mandate bans tree nuts (AN) in strict_allergens mode; 770199 may contain AN
    school_mandate = Mandate(
        id="mandate-school-nutfree",
        buyer_name="Espoo School Catering",
        buyer_type="hospital",
        budget_per_order_eur=1500.0,
        auto_approve_limit_eur=900.0,
        required_claims=["VEGETARIAN"],
        banned_allergens=["AN"],
        strict_allergens=True,
        substitutes_allowed=True,
    )
    granola_need = Need(
        text="100 kg nut-free breakfast granola",
        product_intent="nut-free granola",
        quantity=100,
        unit="kg",
        deliver_by="2026-11-16",
        extra_constraints=[],
    )
    unsafe_granola = Offer(
        product_id="770199",
        name="Crunchy Oat Granola 5kg",
        sales_unit="KI",
        units_needed=20,
        unit_price_eur=18.0,
        comparison_price_eur=3.60,
        comparison_unit="KG",
        total_eur=360.0,
        available_date="2026-11-16",
        closing_time="2026-11-15T14:00:00+02:00",
        sales_block=False,
        logistic_model="Stock",
        evidence={"stock": "verified", "allergens": "declared", "reliability": 0.95},
    )

    subs = rank_substitutes(
        SubstitutesRequest(mandate=school_mandate, need=granola_need, offer=unsafe_granola)
    )
    assert len(subs) == 2
    sub_ids = {s.product_id for s in subs}
    assert "770199" not in sub_ids
    assert sub_ids == {"770210", "770225"}


def test_substitutes_disabled_returns_empty() -> None:
    no_sub_mandate = MANDATE.model_copy(update={"substitutes_allowed": False})
    subs = rank_substitutes(SubstitutesRequest(mandate=no_sub_mandate, need=NEED, offer=OFFER))
    assert subs == []


def test_end_to_end_order_sales_blocked_proposes_ranked_substitutes() -> None:
    blocked_line = json.loads((FIXTURES / "offer.json").read_text())
    blocked_line["sales_block"] = True
    r = client.post(
        "/orders",
        json={
            "mandate_id": "mandate-hotel-001",
            "need": json.loads((FIXTURES / "need.json").read_text()),
            "lines": [blocked_line],
        },
    )
    assert r.status_code == 200
    order = r.json()
    assert order["status"] == "substitutes_proposed"
    assert len(order["substitutes"]) == 3
    assert "[Best:" in order["substitutes"][0]["name"]
    assert "[Better:" in order["substitutes"][1]["name"]
    assert "[Good:" in order["substitutes"][2]["name"]
