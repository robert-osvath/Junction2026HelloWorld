"""P2 (T5-T8): Jury evaluation and substitutes router."""
import os

from fastapi import APIRouter

from app.jury.catalog_lookup import resolve_catalog_items
from app.jury.jurors import _get_env_key, evaluate_with_gemini_decision
from app.jury.substitutes import rank_substitutes
from contracts import JuryRequest, JuryResult, Offer, SubstitutesRequest

router = APIRouter(tags=["jury"])


def _should_use_live_api() -> bool:
    if os.getenv("JURY_LIVE_LLM", "").lower() in ("1", "true", "yes"):
        return True
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    key = _get_env_key("GEMINI_API_KEY", "GOOGLE_API_KEY")
    return key.startswith("AIza")


@router.post("/jury/evaluate")
def evaluate(req: JuryRequest) -> JuryResult:
    catalog_items = resolve_catalog_items([o.product_id for o in req.offers])
    metrics = evaluate_with_gemini_decision(
        mandate=req.mandate,
        need=req.need,
        offers=req.offers,
        catalog_items=catalog_items,
        live_api=_should_use_live_api(),
    )
    return metrics.result


@router.post("/substitutes")
def substitutes(req: SubstitutesRequest) -> list[Offer]:
    return rank_substitutes(req)
