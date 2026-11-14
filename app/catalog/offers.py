"""Offer builder (T4): product ids + need -> priced, availability-checked Offers.

`build_offers` is pure (no I/O); `get_offers` reads the catalog and calls the Aimo client once per API.

Skipped products (unit mismatch, no price/availability, not in catalog, ...) are only logged, not
returned. Content per sales unit is estimated from the price ratio; T2's static
content_per_sales_unit could later cross-check that estimate.
"""
import logging
import math
import sqlite3
from typing import Any, Iterable, Mapping

from app.catalog import store
from app.catalog.aimo import AimoClient, AvailabilityInfo, PriceInfo
from contracts import Evidence, Need, Offer, OffersRequest

log = logging.getLogger(__name__)

_COMPARISON_UNIT = {"L": "L", "kg": "KG"}
_HALF_CENT = 0.005


def roundest_content(catalog_price: float, comparison_price: float) -> float:
    """Content per sales unit (L or kg) from catalogPrice / comparisonPrice.

    comparisonPrice is rounded to cents, so the true content lies in
    [catalog/(comparison+0.005), catalog/(comparison-0.005)]. Return the number in that interval
    with the fewest significant digits (nearest the raw ratio on ties): Eila 31.31/1.57 = 19.94 -> 20.
    """
    if not (catalog_price > 0 and comparison_price > 0) or not math.isfinite(catalog_price + comparison_price):
        raise ValueError(f"prices must be positive and finite: {catalog_price}, {comparison_price}")
    ratio = catalog_price / comparison_price
    if comparison_price <= _HALF_CENT:
        return ratio
    lo = catalog_price / (comparison_price + _HALF_CENT)
    hi = catalog_price / (comparison_price - _HALF_CENT)
    top = math.floor(math.log10(hi))
    for digits in range(1, 8):
        step = 10.0 ** (top - digits + 1)
        first = math.ceil(lo / step - 1e-9)
        cands = [k * step for k in range(first, first + 1000) if k * step <= hi * (1 + 1e-9)]
        if cands:
            return round(min(cands, key=lambda c: abs(c - ratio)), 10)
    return ratio


def round_to_lot(n: int, lot: float | None) -> int:
    """Smallest integer >= n that is a whole multiple of `lot` (lot <= 1: n itself).

    Fractional lots > 1 need larger integer steps: 1.5 -> multiples of 3, 2.5 -> multiples of 5.
    """
    if not lot or lot <= 1:
        return n
    for k in range(1, 1000):
        step = lot * k
        if abs(step - round(step)) < 1e-9:
            step = round(step)
            return max(math.ceil(n / step - 1e-9), 1) * step
    return math.ceil(n / lot) * math.ceil(lot)


def _units_needed(need: Need, row: Mapping[str, Any], price: PriceInfo) -> int | None:
    if need.unit == "pcs":
        if row["sales_unit"] == row["base_unit"]:
            size = 1.0
        else:
            size = row.get("size_in_base_units")
        if not size:
            return None
        n = math.ceil(need.quantity / size - 1e-9)
    else:
        content = roundest_content(price.catalog_price_eur, price.comparison_price_eur)
        n = math.ceil(need.quantity / content - 1e-9)
    return round_to_lot(max(n, 1), row["allowed_lot_size"])


def build_offers(need: Need, product_ids: Iterable[str],
                 catalog_rows: Mapping[str, Mapping[str, Any]],
                 prices: Mapping[str, PriceInfo],
                 availability: Mapping[str, AvailabilityInfo]) -> list[Offer]:
    """One Offer per orderable product, in input order. Unorderable ids are skipped and logged.

    Evidence.stock: Terminal -> "estimated"; Stock -> "verified" if availableAmount >= units_needed,
    else "none" (shortfall: the jury escalates). Unknown quantity on Stock also yields "none".
    allergens: "declared" iff the catalog has allergen data. reliability: catalog value or None.
    """
    offers: list[Offer] = []
    for pid in product_ids:
        pid = str(pid)
        row, price, av = catalog_rows.get(pid), prices.get(pid), availability.get(pid)
        if row is None:
            log.info("skip %s: not in catalog", pid)
            continue
        if price is None or price.catalog_price_eur is None:
            log.info("skip %s: no price", pid)
            continue
        if price.catalog_price_eur <= 0 or not price.comparison_price_eur or price.comparison_price_eur <= 0 \
                or price.comparison_unit not in ("L", "KG"):
            log.info("skip %s: no usable comparison price", pid)
            continue
        if need.unit != "pcs" and price.comparison_unit != _COMPARISON_UNIT[need.unit]:
            log.info("skip %s: comparison unit %s does not match need unit %s",
                     pid, price.comparison_unit, need.unit)
            continue
        if av is None or av.logistic_model is None or av.available_date is None:
            log.info("skip %s: no availability", pid)
            continue
        units = _units_needed(need, row, price)
        if units is None:
            log.info("skip %s: unknown size of sales unit %s in base units", pid, row["sales_unit"])
            continue
        if av.logistic_model == "Terminal":
            stock = "estimated"
        elif av.quantity is not None and av.quantity >= units:
            stock = "verified"
        else:
            stock = "none"
        offers.append(Offer(
            product_id=pid,
            name=row["name_en"] or row["name_fi"] or pid,
            sales_unit=row["sales_unit"],
            units_needed=units,
            unit_price_eur=price.catalog_price_eur,
            comparison_price_eur=price.comparison_price_eur,
            comparison_unit=price.comparison_unit,
            total_eur=round(units * price.catalog_price_eur, 2),
            available_date=av.available_date,
            closing_time=av.closing_time,
            sales_block=av.sales_block,
            logistic_model=av.logistic_model,
            evidence=Evidence(
                stock=stock,
                allergens="declared" if row["has_allergen_data"] else "missing",
                reliability=row["delivery_reliability"],
            ),
        ))
    return offers


class InvalidOfferRequest(ValueError):
    """A client error in the request (maps to HTTP 422); other errors are server faults."""


def get_offers(conn: sqlite3.Connection, client: AimoClient, req: OffersRequest) -> list[Offer]:
    if not (math.isfinite(req.need.quantity) and req.need.quantity > 0):
        raise InvalidOfferRequest("quantity must be a positive finite number")
    ids = list(dict.fromkeys(str(p) for p in req.product_ids))
    rows = store.offer_rows(conn, ids)
    known = [p for p in ids if p in rows]
    prices = client.prices(known, req.delivery_date)
    availability = client.availability(known, req.delivery_date)
    return build_offers(req.need, ids, rows, prices, availability)
