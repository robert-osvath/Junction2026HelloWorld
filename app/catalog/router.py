"""P1 (T1-T4). Mock: returns fixtures."""
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.catalog import store
from app.catalog.aimo import AimoClient, AimoError, get_client
from app.catalog.offers import get_offers
from app.mocks import load
from contracts import CatalogItem, Offer, OffersRequest

router = APIRouter(tags=["catalog"])


@router.get("/catalog/search")
def search(q: str = "", claims: str = "", exclude_allergens: str = "", category: str = "",
           limit: int = 20) -> list[CatalogItem]:
    return [load("catalog_item")][:limit]


@router.post("/offers")
def offers(req: OffersRequest, client: AimoClient = Depends(get_client)) -> list[Offer]:
    conn = store.connect()
    try:
        return get_offers(conn, client, req)
    except AimoError as e:
        logging.getLogger(__name__).warning("aimo error: %s", e)
        raise HTTPException(status_code=502, detail="Valio Aimo API unavailable") from e
    finally:
        conn.close()
