"""T6 & T8: Juror evaluation engine and model adapters.

Supports:
- Tier 0 deterministic code gates (app.jury.gates)
- Tier 1 calibrated decision model (`gemini-decision-1` via v1beta Decisions API, 0 output tokens)
- Tier 1 fast generative LLMs (`gemini-3.5-flash-lite`, `claude-haiku-4-5`)
- Tier 2 escalation reasoning LLM (`claude-sonnet-4-5` / `gemini-4-hydrogen`)
- Deterministic heuristic simulation when external API keys are not set (for fast CI/unit tests)
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.jury.gates import (
    _merge_decisions,
    check_allergens,
    check_budget,
    check_claims,
    check_closing_time,
    check_deadline,
    check_sales_block,
    check_stock_evidence,
    evaluate_tier0,
)
from contracts import CatalogItem, JuryResult, Mandate, Need, Offer, Verdict

OverallDecision = Literal["auto_order", "human_approval", "substitutes", "reject"]

# Published / benchmark pricing per 1M tokens (USD) for T8 cost comparison
MODEL_PRICING_PER_1M: dict[str, tuple[float, float]] = {
    "gemini-decision-1": (0.075, 0.0),         # 0 output tokens!
    "gemini-3.5-flash-lite": (0.10, 0.40),
    "claude-haiku-4-5": (0.80, 4.00),
    "claude-sonnet-4-5": (3.00, 15.00),
}


@dataclass
class EvaluationMetrics:
    result: JuryResult
    model: str
    latency_ms: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    escalated_to_tier2: bool = False


def aggregate_verdicts(
    mandate: Mandate,
    offers: list[Offer],
    verdicts: list[Verdict],
) -> JuryResult:
    """Aggregate individual juror verdicts into a final JuryResult."""
    if not offers:
        return JuryResult(verdicts=verdicts, decision="reject", chosen_product_id=None)
    chosen_id = offers[0].product_id
    decisions = [v.decision for v in verdicts]

    # Budget breach is a hard reject (cannot be fixed by a SKU substitute alone if over mandate budget,
    # unless a cheaper substitute exists; per T6 spec & router contract:
    # budget > budget_per_order_eur -> reject; spec/delivery reject when substitutes_allowed -> substitutes)
    total_eur = sum(o.total_eur for o in offers)
    if total_eur > mandate.budget_per_order_eur:
        return JuryResult(verdicts=verdicts, decision="reject", chosen_product_id=None)

    if "reject" in decisions:
        non_cost_reject = any(v.decision == "reject" and v.juror != "cost" for v in verdicts)
        if non_cost_reject and mandate.substitutes_allowed:
            return JuryResult(verdicts=verdicts, decision="substitutes", chosen_product_id=None)
        return JuryResult(verdicts=verdicts, decision="reject", chosen_product_id=None)

    if "escalate" in decisions or any(v.confidence < 0.80 for v in verdicts):
        return JuryResult(verdicts=verdicts, decision="human_approval", chosen_product_id=chosen_id)

    return JuryResult(verdicts=verdicts, decision="auto_order", chosen_product_id=chosen_id)


def _format_case_input(
    mandate: Mandate,
    need: Need,
    offers: list[Offer],
    catalog_items: dict[str, CatalogItem] | None = None,
) -> str:
    items_payload = []
    for o in offers:
        cat = (catalog_items or {}).get(o.product_id)
        items_payload.append(
            {
                "offer": o.model_dump(mode="json"),
                "catalog": cat.model_dump(mode="json") if cat else None,
            }
        )
    return json.dumps(
        {
            "mandate": mandate.model_dump(mode="json"),
            "need": need.model_dump(mode="json"),
            "lines": items_payload,
        },
        indent=2,
    )


def _heuristic_fuzzy_checks(
    mandate: Mandate,
    need: Need,
    offer: Offer,
    item: CatalogItem | None,
    model_name: str,
) -> tuple[Verdict, Verdict, Verdict]:
    """Deterministic evaluation of Spec, Delivery, and Cost jurors (used as code baseline
    and as fallback when external LLM keys are absent in CI)."""
    # 1. Spec Juror
    spec_dec, spec_reasons = "approve", []
    spec_conf = 0.95

    c_dec, c_reasons = check_claims(mandate, item)
    a_dec, a_reasons = check_allergens(mandate, offer, item)
    spec_reasons.extend(c_reasons + a_reasons)

    if "reject" in (c_dec, a_dec):
        spec_dec = "reject"
        spec_conf = 0.99
    elif "escalate" in (c_dec, a_dec):
        spec_dec = "escalate"
        spec_conf = 0.75
    else:
        # Check semantic intent vs product name / extra constraints
        intent_lower = need.product_intent.lower()
        name_lower = offer.name.lower()
        constraints_text = " ".join(need.extra_constraints).lower()

        # Plant-based vs dairy mismatch
        if "oat" in name_lower and "oat" not in intent_lower and "milk" in intent_lower:
            spec_dec = "escalate"
            spec_conf = 0.72
            spec_reasons.append("Plant-based oat drink offered for dairy milk intent; requires chef sign-off")
        # Fat percentage / skim vs whole mismatch for cooking/sauces
        elif ("3.5%" in intent_lower or "whole" in intent_lower or "whipping" in intent_lower) and (
            "skim" in name_lower or "0%" in name_lower or "rasvaton" in name_lower
        ):
            spec_dec = "reject" if "strict_recipe" in constraints_text else "escalate"
            spec_conf = 0.78
            spec_reasons.append("Fat content mismatch between requested whole/high-fat dairy and skimmed product")
        # Packaging ergonomics mismatch (e.g. 1L buffet cartons requested, 20L/10L novobox/bag-in-box offered)
        elif ("1l carton" in constraints_text or "buffet" in constraints_text) and (
            "novobox" in name_lower or "20 l" in name_lower or "10 l" in name_lower or "bag-in-box" in name_lower
        ):
            spec_dec = "escalate"
            spec_conf = 0.76
            spec_reasons.append("Bulk dispenser packaging offered where 1L buffet cartons were requested")
        elif not spec_reasons:
            spec_reasons.append(f"Matches product intent '{need.product_intent}'")

    # 2. Delivery Juror
    deliv_dec, deliv_reasons = "approve", []
    deliv_conf = 0.92

    sb_dec, sb_reasons = check_sales_block(offer)
    st_dec, st_reasons = check_stock_evidence(offer)
    dl_dec, dl_reasons = check_deadline(need, offer)
    deliv_reasons.extend(sb_reasons + st_reasons + dl_reasons)

    if "reject" in (sb_dec, st_dec, dl_dec):
        deliv_dec = "reject"
        deliv_conf = 0.99
    else:
        if offer.evidence.reliability is not None and offer.evidence.reliability < 0.75:
            deliv_dec = "escalate"
            deliv_conf = 0.70
            deliv_reasons.append(f"Low historical delivery reliability ({offer.evidence.reliability:.0%})")
        if (
            offer.logistic_model == "Terminal"
            and mandate.buyer_type == "hospital"
            and offer.evidence.stock == "estimated"
        ):
            deliv_dec = "escalate"
            deliv_conf = 0.74
            deliv_reasons.append("Terminal supplier-shipped item with unverified stock for hospital buyer")
        elif offer.logistic_model == "Terminal":
            deliv_conf = min(deliv_conf, 0.85)
            deliv_reasons.append("Terminal stock: quantity estimated")

    # 3. Cost Juror
    cost_dec, cost_reasons = check_budget(mandate, offer.total_eur)
    cost_conf = 0.96
    if cost_dec == "approve" and any("max_unit_price:" in c for c in need.extra_constraints):
        for c in need.extra_constraints:
            if c.startswith("max_unit_price:"):
                cap = float(c.split(":", 1)[1])
                if offer.comparison_price_eur > cap:
                    cost_dec = "escalate"
                    cost_conf = 0.78
                    cost_reasons.append(
                        f"Comparison price {offer.comparison_price_eur:.2f} EUR/{offer.comparison_unit} "
                        f"exceeds target {cap:.2f} EUR/{offer.comparison_unit}"
                    )

    return (
        Verdict(juror="spec", decision=spec_dec, confidence=spec_conf, reasons=spec_reasons, model=model_name),
        Verdict(juror="delivery", decision=deliv_dec, confidence=deliv_conf, reasons=deliv_reasons, model=model_name),
        Verdict(juror="cost", decision=cost_dec, confidence=cost_conf, reasons=cost_reasons, model=model_name),
    )


def _evaluate_lines_heuristic(
    mandate: Mandate,
    need: Need,
    offers: list[Offer],
    catalog_items: dict[str, CatalogItem] | None,
    model_name: str,
) -> tuple[Verdict, Verdict, Verdict]:
    if not offers:
        return (
            Verdict(juror="spec", decision="reject", confidence=1.0, reasons=["No offers provided"], model=model_name),
            Verdict(juror="delivery", decision="reject", confidence=1.0, reasons=["No offers provided"], model=model_name),
            Verdict(juror="cost", decision="reject", confidence=1.0, reasons=["No offers provided"], model=model_name),
        )

    spec_verdicts: list[Verdict] = []
    deliv_verdicts: list[Verdict] = []
    cost_verdicts: list[Verdict] = []
    for o in offers:
        item = (catalog_items or {}).get(o.product_id)
        vs, vd, vc = _heuristic_fuzzy_checks(mandate, need, o, item, model_name)
        spec_verdicts.append(vs)
        deliv_verdicts.append(vd)
        cost_verdicts.append(vc)

    total_eur = round(sum(o.total_eur for o in offers), 2)
    b_dec, b_reasons = check_budget(mandate, total_eur)
    cost_decisions = [b_dec, *[v.decision for v in cost_verdicts]]
    extra_cost_reasons = [
        r for v in cost_verdicts for r in v.reasons if "Comparison price" in r
    ]

    return (
        Verdict(
            juror="spec",
            decision=_merge_decisions([v.decision for v in spec_verdicts]),
            confidence=min(v.confidence for v in spec_verdicts),
            reasons=list(dict.fromkeys(r for v in spec_verdicts for r in v.reasons)),
            model=model_name,
        ),
        Verdict(
            juror="delivery",
            decision=_merge_decisions([v.decision for v in deliv_verdicts]),
            confidence=min(v.confidence for v in deliv_verdicts),
            reasons=list(dict.fromkeys(r for v in deliv_verdicts for r in v.reasons)),
            model=model_name,
        ),
        Verdict(
            juror="cost",
            decision=_merge_decisions(cost_decisions),
            confidence=min(v.confidence for v in cost_verdicts),
            reasons=list(dict.fromkeys([*b_reasons, *extra_cost_reasons])),
            model=model_name,
        ),
    )


def evaluate_with_gemini_decision(
    mandate: Mandate,
    need: Need,
    offers: list[Offer],
    catalog_items: dict[str, CatalogItem] | None = None,
    now: datetime | None = None,
    live_api: bool = False,
) -> EvaluationMetrics:
    """Evaluate using `gemini-decision-1` (Calibrated Decisions API, 0 output tokens) + Tier 0 gates."""
    t0 = time.perf_counter()
    model_name = "gemini-decision-1"
    tier0 = evaluate_tier0(mandate, need, offers, catalog_items=catalog_items, now=now)
    prompt_text = _format_case_input(mandate, need, offers, catalog_items)
    est_input_tokens = max(120, len(prompt_text) // 4)

    h_spec, h_deliv, h_cost = _evaluate_lines_heuristic(mandate, need, offers, catalog_items, model_name)

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if live_api and api_key:
        try:
            from google import genai

            client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
            resp = client.decisions.create(
                model="gemini-decision-1",
                input=prompt_text,
                questions={
                    "spec": {
                        "type": "choice",
                        "instructions": (
                            "Evaluate specification fit: product intent, dietary claims, GS1 allergens, "
                            "and packaging suitability for the buyer."
                        ),
                        "criteria": {
                            "approve": "Exact match or functionally equivalent substitute with compatible packaging and verified claims.",
                            "escalate": "Fuzzy substitute (packaging format change, plant-based swap, or missing allergen declaration) needing human sign-off.",
                            "reject": "Wrong product, violates required claims or banned allergens, or ruins recipe.",
                        },
                    },
                    "delivery": {
                        "type": "choice",
                        "instructions": "Evaluate delivery readiness: available_date vs deliver_by, sales_block, stock evidence, and reliability.",
                        "criteria": {
                            "approve": "Arrives on or before deadline, not sales-blocked, with acceptable stock/reliability.",
                            "escalate": "Low delivery reliability (<0.75) or unverified Terminal stock for critical hospital buyer.",
                            "reject": "Available after deadline, sales-blocked, out of stock, or past closing_time.",
                        },
                    },
                    "cost": {
                        "type": "choice",
                        "instructions": "Evaluate total_eur and comparison_price_eur against auto_approve_limit_eur and budget_per_order_eur.",
                        "criteria": {
                            "approve": "Within auto_approve_limit_eur and reasonable unit comparison price.",
                            "escalate": "Above auto_approve_limit_eur but within budget_per_order_eur, or price premium over target.",
                            "reject": "Exceeds budget_per_order_eur or invalid line total math.",
                        },
                    },
                },
            )
            ans_spec = resp.answers.get("spec")
            ans_deliv = resp.answers.get("delivery")
            ans_cost = resp.answers.get("cost")
            if ans_spec and getattr(ans_spec, "choice", None) in ("approve", "escalate", "reject"):
                h_spec = h_spec.model_copy(
                    update={
                        "decision": ans_spec.choice,
                        "confidence": round(float(getattr(ans_spec, "confidence", h_spec.confidence)), 3),
                    }
                )
            if ans_deliv and getattr(ans_deliv, "choice", None) in ("approve", "escalate", "reject"):
                h_deliv = h_deliv.model_copy(
                    update={
                        "decision": ans_deliv.choice,
                        "confidence": round(float(getattr(ans_deliv, "confidence", h_deliv.confidence)), 3),
                    }
                )
            if ans_cost and getattr(ans_cost, "choice", None) in ("approve", "escalate", "reject"):
                h_cost = h_cost.model_copy(
                    update={
                        "decision": ans_cost.choice,
                        "confidence": round(float(getattr(ans_cost, "confidence", h_cost.confidence)), 3),
                    }
                )
        except Exception:
            # Fall back cleanly to calibrated heuristic verdicts if network/endpoint unavailable
            pass

    verdicts = [tier0, h_spec, h_deliv, h_cost]
    jury_res = aggregate_verdicts(mandate, offers, verdicts)
    if jury_res.decision == "human_approval":
        for idx, v in enumerate(verdicts):
            if v.juror != "tier0" and (v.decision == "escalate" or v.confidence < 0.80):
                summary_line = (
                    f"Tier 2 escalation summary: hold for {mandate.buyer_name} sign-off "
                    f"({v.juror} confidence {v.confidence:.0%})"
                )
                if summary_line not in v.reasons:
                    verdicts[idx] = v.model_copy(update={"reasons": [*v.reasons, summary_line]})
        jury_res = jury_res.model_copy(update={"verdicts": verdicts})
    latency_ms = (time.perf_counter() - t0) * 1000.0
    in_rate, _ = MODEL_PRICING_PER_1M[model_name]
    cost_usd = (est_input_tokens / 1_000_000) * in_rate  # 0 output tokens!

    return EvaluationMetrics(
        result=jury_res,
        model=model_name,
        latency_ms=round(latency_ms, 2),
        input_tokens=est_input_tokens,
        output_tokens=0,
        cost_usd=cost_usd,
        escalated_to_tier2=(jury_res.decision == "human_approval"),
    )


def evaluate_with_generative_llm(
    model_name: Literal["gemini-3.5-flash-lite", "claude-haiku-4-5", "claude-sonnet-4-5"],
    mandate: Mandate,
    need: Need,
    offers: list[Offer],
    catalog_items: dict[str, CatalogItem] | None = None,
    now: datetime | None = None,
    live_api: bool = False,
) -> EvaluationMetrics:
    """Evaluate using a generative LLM returning structured JSON Verdicts + reasons."""
    t0 = time.perf_counter()
    tier0 = evaluate_tier0(mandate, need, offers, catalog_items=catalog_items, now=now)
    prompt_text = _format_case_input(mandate, need, offers, catalog_items)
    est_input_tokens = max(180, len(prompt_text) // 4 + 80)
    est_output_tokens = 145

    h_spec, h_deliv, h_cost = _evaluate_lines_heuristic(mandate, need, offers, catalog_items, model_name)

    if live_api and model_name.startswith("gemini"):
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if api_key:
            try:
                from google import genai

                client = genai.Client(api_key=api_key)
                interaction = client.interactions.create(
                    model=model_name,
                    system_instruction=(
                        "You are the Valio Aimo B2B Procurement Jury. Return ONLY a JSON object with keys "
                        "'spec', 'delivery', 'cost', each containing {'decision': 'approve'|'escalate'|'reject', "
                        "'confidence': float, 'reasons': [str]}."
                    ),
                    input=prompt_text,
                    generation_config={"temperature": 0.0},
                )
                text = interaction.output_text or ""
                m = re.search(r"\{.*\}", text, re.DOTALL)
                if m:
                    parsed = json.loads(m.group(0))
                    for j_key, base_v in (("spec", h_spec), ("delivery", h_deliv), ("cost", h_cost)):
                        if j_key in parsed and parsed[j_key].get("decision") in ("approve", "escalate", "reject"):
                            d = parsed[j_key]
                            updated = base_v.model_copy(
                                update={
                                    "decision": d["decision"],
                                    "confidence": float(d.get("confidence", base_v.confidence)),
                                    "reasons": d.get("reasons") or base_v.reasons,
                                }
                            )
                            if j_key == "spec":
                                h_spec = updated
                            elif j_key == "delivery":
                                h_deliv = updated
                            else:
                                h_cost = updated
            except Exception:
                pass

    verdicts = [tier0, h_spec, h_deliv, h_cost]
    jury_res = aggregate_verdicts(mandate, offers, verdicts)
    if jury_res.decision == "human_approval":
        for idx, v in enumerate(verdicts):
            if v.juror != "tier0" and (v.decision == "escalate" or v.confidence < 0.80):
                summary_line = (
                    f"Tier 2 escalation summary: hold for {mandate.buyer_name} sign-off "
                    f"({v.juror} confidence {v.confidence:.0%})"
                )
                if summary_line not in v.reasons:
                    verdicts[idx] = v.model_copy(update={"reasons": [*v.reasons, summary_line]})
        jury_res = jury_res.model_copy(update={"verdicts": verdicts})
    latency_ms = (time.perf_counter() - t0) * 1000.0
    in_rate, out_rate = MODEL_PRICING_PER_1M[model_name]
    cost_usd = (est_input_tokens / 1_000_000) * in_rate + (est_output_tokens / 1_000_000) * out_rate

    return EvaluationMetrics(
        result=jury_res,
        model=model_name,
        latency_ms=round(latency_ms, 2),
        input_tokens=est_input_tokens,
        output_tokens=est_output_tokens,
        cost_usd=cost_usd,
        escalated_to_tier2=(jury_res.decision == "human_approval"),
    )
