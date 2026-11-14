from __future__ import annotations

import pytest

from contracts.models import CatalogItem, JuryRequest, Mandate, Need, Offer
from scripts.run_t8_benchmark import (
    CANDIDATE_MODELS,
    load_benchmark_cases,
    run_benchmark,
    run_single_case,
)


def test_benchmark_fixture_has_20_valid_cases() -> None:
    cases = load_benchmark_cases()
    assert len(cases) == 20, f"Expected 20 benchmark cases, found {len(cases)}"

    ids = [c["case_id"] for c in cases]
    assert len(set(ids)) == 20, "Benchmark case IDs must be unique"

    for c in cases:
        mandate = Mandate.model_validate(c["mandate"])
        need = Need.model_validate(c["need"])
        offer = Offer.model_validate(c["offer"])
        catalog_item = CatalogItem.model_validate(c["catalog_item"])
        req = JuryRequest(mandate=mandate, need=need, offers=[offer])
        assert req.mandate.id == mandate.id
        assert catalog_item.product_id == offer.product_id


@pytest.mark.parametrize("case", load_benchmark_cases(), ids=lambda c: c["case_id"])
def test_gemini_decision_1_on_all_20_cases(case: dict) -> None:
    res = run_single_case(case, "gemini-decision-1", use_live_api=False)
    assert res.actual_decision == case["expected_decision"], (
        f"{case['case_id']} ({case['description']}): expected decision {case['expected_decision']}, "
        f"got {res.actual_decision}"
    )
    assert res.output_tokens == 0, "gemini-decision-1 must emit 0 output tokens"


def test_benchmark_comparison_metrics_and_safety() -> None:
    summaries = run_benchmark(use_live_api=False)
    assert set(summaries.keys()) == set(CANDIDATE_MODELS)

    gd = summaries["gemini-decision-1"]
    flash = summaries["gemini-3.5-flash-lite"]
    haiku = summaries["claude-haiku-4-5"]
    sonnet = summaries["claude-sonnet-4-5"]

    # All models guarded by Tier 0 + aligned Tier 1 logic achieve 0% safety false-approves
    for model_name, s in summaries.items():
        assert s.total_cases == 20
        assert s.safety_critical_cases >= 5
        assert s.safety_false_approves == 0, f"{model_name} had safety false-approves!"
        assert s.safety_false_approve_pct == 0.0
        assert s.gold_agreement_pct >= 95.0
        assert s.sonnet_agreement_pct >= 95.0

    # gemini-decision-1 uses 0 output tokens and is the most cost-effective & lowest latency
    assert gd.avg_output_tokens == 0.0
    assert gd.cost_per_1k_decisions_usd < flash.cost_per_1k_decisions_usd
    assert flash.cost_per_1k_decisions_usd < haiku.cost_per_1k_decisions_usd
    assert haiku.cost_per_1k_decisions_usd < sonnet.cost_per_1k_decisions_usd
