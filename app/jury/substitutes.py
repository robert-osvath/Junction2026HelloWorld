"""T7: Substitute Ranker (`POST /substitutes`).

Spec:
- Candidates: explicit substitution rules + same category with same required claims and no banned allergens
- Rank by: claim match, comparison-price delta, available by deadline, reliability
- Return top 3 (Best / Better / Good) with a one-line trade-off each
- Substitutes go back through the jury (only candidates that clear Tier 0 / Jury without rejection are returned)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from app.jury.catalog_lookup import (
    EXPLICIT_SUBSTITUTIONS,
    CandidateRecord,
    get_candidate_records,
    resolve_catalog_items,
)
from app.jury.jurors import evaluate_with_gemini_decision
from contracts import CatalogItem, Mandate, Need, Offer, SubstitutesRequest

TierLabel = Literal["Best", "Better", "Good"]
TIER_LABELS: tuple[TierLabel, ...] = ("Best", "Better", "Good")


@dataclass
class RankedSubstitute:
    tier: TierLabel
    score: float
    tradeoff: str
    is_explicit_rule: bool
    jury_decision: str
    offer: Offer


def _infer_category(offer: Offer, need: Need, catalog_map: dict[str, CatalogItem]) -> str:
    orig_cat = catalog_map.get(offer.product_id)
    if orig_cat and orig_cat.category:
        return orig_cat.category
    intent = (need.product_intent + " " + offer.name).lower()
    if "granola" in intent or "cereal" in intent or "muesli" in intent:
        return "20100"
    if "oat" in intent or "oddlygood" in intent:
        return "10490"
    if "cream" in intent or "kerma" in intent:
        return "10410"
    return "10400"


def _passes_claim_and_allergen_prefilter(mandate: Mandate, item: CatalogItem) -> bool:
    if mandate.required_claims:
        present = set(item.claims)
        if any(c not in present for c in mandate.required_claims):
            return False

    if mandate.banned_allergens:
        if not item.allergen_data:
            return False
        banned = set(mandate.banned_allergens)
        if any(a in banned for a in item.allergens_contains):
            return False
        if mandate.strict_allergens and any(a in banned for a in item.allergens_may_contain):
            return False

    return True


def _build_candidate_offer(rec: CandidateRecord, need: Need) -> Offer:
    units_needed = max(1, math.ceil(need.quantity / rec.content_per_sales_unit))
    total_eur = round(units_needed * rec.unit_price_eur, 2)
    return Offer(
        product_id=rec.catalog.product_id,
        name=rec.catalog.name,
        sales_unit=rec.sales_unit,
        units_needed=units_needed,
        unit_price_eur=rec.unit_price_eur,
        comparison_price_eur=rec.comparison_price_eur,
        comparison_unit=rec.comparison_unit,  # type: ignore[arg-type]
        total_eur=total_eur,
        available_date=rec.available_date,
        closing_time=rec.closing_time,
        sales_block=rec.sales_block,
        logistic_model=rec.logistic_model,  # type: ignore[arg-type]
        evidence=rec.evidence,
    )


def _score_candidate(
    rec: CandidateRecord,
    cand_offer: Offer,
    req: SubstitutesRequest,
    is_explicit: bool,
) -> tuple[float, str]:
    # 1. Claim match & intent alignment (0..1)
    req_claims = set(req.mandate.required_claims)
    cand_claims = set(rec.catalog.claims)
    claim_score = 1.0 if req_claims.issubset(cand_claims) else 0.0
    extra_claims = len(cand_claims - req_claims)
    claim_score = min(1.0, claim_score * 0.9 + min(0.1, extra_claims * 0.03))

    # 2. Comparison price delta vs original offer (0..1)
    orig_comp = max(0.01, req.offer.comparison_price_eur)
    pct_delta = (cand_offer.comparison_price_eur - orig_comp) / orig_comp
    # If cheaper or equal, score near 1.0; if more expensive, decay smoothly
    price_score = max(0.0, min(1.0, 0.85 - pct_delta * 1.2))

    # 3. Available by deadline & stock verification (0..1)
    if cand_offer.available_date > req.need.deliver_by or cand_offer.sales_block:
        delivery_score = 0.0
    else:
        days_early = max(0, (req.need.deliver_by - cand_offer.available_date).days)
        stock_bonus = 0.20 if cand_offer.evidence.stock == "verified" else 0.05
        delivery_score = min(1.0, 0.75 + stock_bonus + min(0.05, days_early * 0.02))

    # 4. Historical delivery reliability (0..1)
    reliability = cand_offer.evidence.reliability if cand_offer.evidence.reliability is not None else 0.80
    reliability_score = max(0.0, min(1.0, reliability))

    explicit_bonus = 0.08 if is_explicit else 0.0
    composite = (
        0.35 * claim_score
        + 0.25 * price_score
        + 0.25 * delivery_score
        + 0.15 * reliability_score
        + explicit_bonus
    )

    # Format concise one-line trade-off
    sign = "+" if pct_delta > 0 else ""
    price_str = (
        f"same {cand_offer.comparison_price_eur:.2f} EUR/{cand_offer.comparison_unit}"
        if abs(pct_delta) < 0.005
        else f"{sign}{pct_delta * 100:.1f}% ({cand_offer.comparison_price_eur:.2f} EUR/{cand_offer.comparison_unit})"
    )
    rule_str = "explicit rule" if is_explicit else "same-category match"
    rel_str = f"{reliability * 100:.0f}% rel" if cand_offer.evidence.reliability is not None else "est. rel"
    tradeoff = f"{rule_str}, {price_str}, {cand_offer.evidence.stock} stock, {rel_str}"

    return round(composite, 4), tradeoff


def rank_substitutes_detailed(req: SubstitutesRequest, top_k: int = 3) -> list[RankedSubstitute]:
    """Rank candidate substitutes and screen each one back through the Jury."""
    if not req.mandate.substitutes_allowed:
        return []
    if not (math.isfinite(req.need.quantity) and req.need.quantity > 0) or top_k <= 0:
        return []

    records = get_candidate_records()
    catalog_map = resolve_catalog_items(list(records.keys()) + [req.offer.product_id])
    target_category = _infer_category(req.offer, req.need, catalog_map)

    explicit_ids = set(EXPLICIT_SUBSTITUTIONS.get(req.offer.product_id, []))

    # Gather candidate pool: explicit rules + same category
    candidate_pool: list[tuple[CandidateRecord, bool]] = []
    seen_ids: set[str] = {req.offer.product_id}

    for pid in EXPLICIT_SUBSTITUTIONS.get(req.offer.product_id, []):
        if pid in records and pid not in seen_ids:
            candidate_pool.append((records[pid], True))
            seen_ids.add(pid)

    for pid, rec in records.items():
        if pid in seen_ids:
            continue
        if rec.catalog.category == target_category:
            candidate_pool.append((rec, pid in explicit_ids))
            seen_ids.add(pid)

    scored: list[tuple[float, str, bool, str, Offer]] = []
    for rec, is_explicit in candidate_pool:
        if not _passes_claim_and_allergen_prefilter(req.mandate, rec.catalog):
            continue

        cand_offer = _build_candidate_offer(rec, req.need)

        # Spec requirement: "Substitutes go back through the jury"
        jury_metrics = evaluate_with_gemini_decision(
            mandate=req.mandate,
            need=req.need,
            offers=[cand_offer],
            catalog_items={rec.catalog.product_id: rec.catalog},
            live_api=False,
        )
        jury_decision = jury_metrics.result.decision
        if jury_decision in ("reject", "substitutes"):
            continue

        score, tradeoff = _score_candidate(rec, cand_offer, req, is_explicit)
        # Prefer auto_order candidates over human_approval candidates
        if jury_decision == "auto_order":
            score += 0.05
        scored.append((score, tradeoff, is_explicit, jury_decision, cand_offer))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_items = scored[:top_k]

    ranked: list[RankedSubstitute] = []
    for idx, (score, tradeoff, is_explicit, jury_decision, cand_offer) in enumerate(top_items):
        tier: TierLabel = TIER_LABELS[min(idx, len(TIER_LABELS) - 1)]
        annotated_offer = cand_offer.model_copy(
            update={"name": f"{cand_offer.name} [{tier}: {tradeoff}]"}
        )
        ranked.append(
            RankedSubstitute(
                tier=tier,
                score=score,
                tradeoff=tradeoff,
                is_explicit_rule=is_explicit,
                jury_decision=jury_decision,
                offer=annotated_offer,
            )
        )
    return ranked


def rank_substitutes(req: SubstitutesRequest, top_k: int = 3) -> list[Offer]:
    """Return top-3 (Best / Better / Good) substitute Offers with one-line trade-offs."""
    return [r.offer for r in rank_substitutes_detailed(req, top_k=top_k)]
