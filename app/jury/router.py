"""P2 (T5-T8). Mock: returns fixtures."""
from fastapi import APIRouter

from app.mocks import load
from contracts import JuryRequest, JuryResult, Offer, SubstitutesRequest

router = APIRouter(tags=["jury"])


@router.post("/jury/evaluate")
def evaluate(req: JuryRequest) -> JuryResult:
    return load("jury_result")


@router.post("/substitutes")
def substitutes(req: SubstitutesRequest) -> list[Offer]:
    return [load("offer")]
