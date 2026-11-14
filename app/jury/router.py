"""P2 (T5-T8). Mock: returns fixtures."""
from fastapi import APIRouter

from app.mocks import load
from contracts import JuryRequest, JuryResult, Offer, SubstitutesRequest

router = APIRouter(tags=["jury"])


@router.post("/jury/evaluate")
def evaluate(req: JuryRequest) -> JuryResult:
    # mock until T6: fixture verdicts, decision from the order total vs the mandate
    total = sum(o.total_eur for o in req.offers)
    m = req.mandate
    decision = "reject" if total > m.budget_per_order_eur else "human_approval" if total > m.auto_approve_limit_eur else "auto_order"
    return load("jury_result").model_copy(update={"decision": decision})


@router.post("/substitutes")
def substitutes(req: SubstitutesRequest) -> list[Offer]:
    return [load("offer")]
