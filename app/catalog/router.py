"""Catalog discovery backed by the imported Valio Aimo dataset."""
from fastapi import APIRouter

from app.catalog.aimo import build_offers
from app.catalog.store import connect, search_products
from app.mocks import load
from contracts import CatalogItem, Offer, OffersRequest

router = APIRouter(tags=["catalog"])


@router.get("/catalog/search")
def search(q: str = "", claims: str = "", exclude_allergens: str = "", category: str = "",
           limit: int = 20) -> list[CatalogItem]:
    with connect() as conn:
        has_catalog = conn.execute("SELECT EXISTS(SELECT 1 FROM products)").fetchone()[0]
        if not has_catalog:
            # Keeps the starter project runnable before the supplied data has
            # been imported; normal operation always uses the dataset above.
            return [load("catalog_item")][:limit]
        return [CatalogItem.model_validate(item) for item in search_products(
            conn,
            query=q,
            claims=[value.strip() for value in claims.split(",")],
            excluded_allergens=[value.strip() for value in exclude_allergens.split(",")],
            category=category,
            limit=limit,
        )]


@router.post("/offers")
def offers(req: OffersRequest) -> list[Offer]:
    return build_offers(req)
