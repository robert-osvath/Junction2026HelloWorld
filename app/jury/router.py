"""P2 (T5-T8). Mock: returns fixtures."""
from fastapi import APIRouter

from app.mocks import load
from contracts import JuryRequest, JuryResult, Offer, SubstitutesRequest, Verdict

router = APIRouter(tags=["jury"])


@router.post("/jury/evaluate")
def evaluate(req: JuryRequest) -> JuryResult:
    # mock until T6: fixture verdicts, decision from the order total vs the mandate
    total = sum(o.total_eur for o in req.offers)
    m = req.mandate
    decision = "reject" if total > m.budget_per_order_eur else "human_approval" if total > m.auto_approve_limit_eur else "auto_order"
    res = load("jury_result")
    # keep the cost verdict consistent with the decision (fixture reasons assume auto-approve)
    cost = Verdict(juror="cost", decision={"auto_order": "approve", "human_approval": "escalate"}.get(decision, "reject"),
                   confidence=1.0, model=None, reasons=[f"Total {total:,.2f} EUR vs auto-approve limit "
                   f"{m.auto_approve_limit_eur:,.2f} and budget {m.budget_per_order_eur:,.2f}"])
    verdicts = [cost if v.juror == "cost" else v for v in res.verdicts]
    return res.model_copy(update={"decision": decision, "verdicts": verdicts})


@router.post("/substitutes")
def substitutes(req: SubstitutesRequest) -> list[Offer]:
    return [load("offer")]
