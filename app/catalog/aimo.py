"""Small, typed adapter for the challenge's live synthetic offer feeds."""
import math
import os
from datetime import datetime

import httpx
from fastapi import HTTPException
from dotenv import load_dotenv

from app.catalog.store import connect, products_by_id
from contracts import Evidence, Offer, OffersRequest

BASE_URL = "https://junction26.valioaimo.fi"


def _value(item: dict, key: str) -> float | None:
    value = item.get(key)
    return float(value["value"]) if isinstance(value, dict) and value.get("value") is not None else None


def build_offers(request: OffersRequest) -> list[Offer]:
    """Fetch current price and availability for an explicitly selected shortlist.

    The user-visible API never accepts a price from the voice model. It reads
    both synthetic feeds immediately before jury evaluation.
    """
    load_dotenv()
    api_key = os.getenv("VALIO_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="Valio Aimo live pricing is not configured")
    with connect() as conn:
        metadata = products_by_id(conn, request.product_ids)
    missing = [product_id for product_id in request.product_ids if product_id not in metadata]
    if missing:
        raise HTTPException(status_code=404, detail=f"Unknown catalog product(s): {', '.join(missing)}")
    body = {
        "customer": os.getenv("VALIO_CUSTOMER", "VIMO-DEMO"),
        "requestedDate": request.delivery_date.isoformat(),
        "products": request.product_ids,
    }
    try:
        with httpx.Client(base_url=BASE_URL, headers={"X-API-Key": api_key}, timeout=20) as client:
            price_response = client.post("/api/price", json=body)
            availability_response = client.post("/api/availability", json=body)
            price_response.raise_for_status()
            availability_response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Valio Aimo price or availability feed is unavailable") from exc

    prices = {item["productId"]: item for item in price_response.json().get("products", [])}
    availability = {item["productId"]: item for item in availability_response.json().get("products", [])}
    offers: list[Offer] = []
    for product_id in request.product_ids:
        price = prices.get(product_id, {})
        stock = availability.get(product_id, {})
        catalog_price = _value(price, "catalogPrice")
        comparison_price = _value(price, "comparisonPrice")
        lines = stock.get("scheduleLines") or []
        line = lines[0] if lines else {}
        if price.get("error") or catalog_price is None or comparison_price is None or not line.get("availableDate"):
            continue
        comparison_unit = price.get("comparisonUnit")
        if comparison_unit not in {"KG", "L"}:
            continue
        # The live price ratio is the documented best approximation for the
        # amount in one sales unit. Round to a sensible package amount before
        # applying ceil so a rounded comparison price does not over-order.
        price_ratio = catalog_price / comparison_price
        content_per_sales_unit = max(1.0, float(round(price_ratio)) if price_ratio >= 1 else round(price_ratio, 2))
        compatible_unit = request.need.unit.upper() == comparison_unit
        units_needed = math.ceil(request.need.quantity / content_per_sales_unit) if compatible_unit else math.ceil(request.need.quantity)
        product = metadata[product_id]
        logistic_model = stock.get("logisticModel", "Terminal")
        offers.append(Offer(
            product_id=product_id,
            name=product.get("name_en") or product.get("name_fi") or product_id,
            sales_unit=line.get("salesUnit") or product["sales_unit"],
            units_needed=units_needed,
            unit_price_eur=catalog_price,
            comparison_price_eur=comparison_price,
            comparison_unit=comparison_unit,
            total_eur=round(units_needed * catalog_price, 2),
            available_date=line["availableDate"],
            closing_time=datetime.fromisoformat(stock["closingTime"]) if stock.get("closingTime") else None,
            sales_block=bool(stock.get("salesBlock") or line.get("salesBlock")),
            logistic_model=logistic_model if logistic_model in {"Stock", "Terminal"} else "Terminal",
            evidence=Evidence(
                stock="verified" if logistic_model == "Stock" else "estimated",
                allergens="declared" if product["has_allergen_data"] else "missing",
                reliability=product["delivery_reliability"],
            ),
        ))
    return offers
