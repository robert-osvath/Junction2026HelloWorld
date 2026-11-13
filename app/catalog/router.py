"""P1 (T1-T4). Mock: returns fixtures."""
from fastapi import APIRouter

from app.mocks import load
from contracts import CatalogItem, Offer, OffersRequest

router = APIRouter(tags=["catalog"])


@router.get("/catalog/search")
def search(q: str = "", claims: str = "", exclude_allergens: str = "", category: str = "",
           limit: int = 20) -> list[CatalogItem]:
    return [load("catalog_item")][:limit]


@router.post("/offers")
def offers(req: OffersRequest) -> list[Offer]:
    return [load("offer")]
