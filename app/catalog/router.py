"""P1 (T1-T4). /catalog/search is real (T2); /offers is still a mock (T4)."""
from typing import Literal

from fastapi import APIRouter

from app.catalog import store
from app.catalog.search import search as run_search

from app.mocks import load
from contracts import CatalogItem, Offer, OffersRequest

router = APIRouter(tags=["catalog"])


@router.get("/catalog/search")
def search(q: str = "", claims: str = "", exclude_allergens: str = "", category: str = "",
           limit: int = 20,
           quantity: float | None = None, unit: Literal["L", "kg", "pcs"] | None = None) -> list[CatalogItem]:
    conn = store.connect()  # per request: sqlite connections are not shared across threads
    try:
        return run_search(conn, q, claims, exclude_allergens, category, limit, quantity, unit)
    finally:
        conn.close()


@router.post("/offers")
def offers(req: OffersRequest) -> list[Offer]:
    return [load("offer")]
