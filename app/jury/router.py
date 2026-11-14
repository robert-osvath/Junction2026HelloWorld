"""P2 (T5-T8): Jury evaluation and substitutes router."""
from fastapi import APIRouter

from app.jury.catalog_lookup import resolve_catalog_items
from app.jury.jurors import evaluate_with_gemini_decision
from app.jury.substitutes import rank_substitutes
from contracts import JuryRequest, JuryResult, Offer, SubstitutesRequest

router = APIRouter(tags=["jury"])


@router.post("/jury/evaluate")
def evaluate(req: JuryRequest) -> JuryResult:
    catalog_items = resolve_catalog_items([o.product_id for o in req.offers])
    metrics = evaluate_with_gemini_decision(
        mandate=req.mandate,
        need=req.need,
        offers=req.offers,
        catalog_items=catalog_items,
        live_api=False,
    )
    return metrics.result


@router.post("/substitutes")
def substitutes(req: SubstitutesRequest) -> list[Offer]:
    return rank_substitutes(req)
