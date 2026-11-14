"""Catalog search: full-text candidates from store.py, reranked here, mapped to CatalogItem.

Ranking (tuple, higher is better), all generic:
1. how many query terms occur in the product NAME (fi/en/sv), then anywhere indexed;
2. pack fit for the requested quantity (only when `quantity` and `unit` L/kg are given, see `_fit`);
3. bm25 relevance (name columns weighted far above ingredients, see store.FTS_WEIGHTS).
A vector score can later be blended in as an extra key of `_rank` without changing the signature.
"""
import math
import sqlite3

from app.catalog import store
from contracts import CatalogItem

CANDIDATES = 1000
MAX_LIMIT = 100


def _csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def _hits(text: str, terms: list[str]) -> int:
    text = text.lower()
    return sum(t in text for t in terms)


_UNITS = {"l": "L", "kg": "KG"}  # request unit -> products.content_unit ('pcs' has no pack content)


def _fit(row: sqlite3.Row, quantity: float | None, unit: str | None):
    """Pack fit for an order of `quantity` `unit`, as a sort key (higher is better).
    Lets a wholesale buyer's size drive the order: with content c per sales unit, ordering needs
    n = ceil(quantity / c) units, and cost = log(n) + log(c / quantity) if c > quantity else log(n).
    So fewer units is better (log n), and one oversized pack pays for the waste (log of the overshoot).
    Products with unknown or other-unit content rank after those with a computed fit, never excluded."""
    want = _UNITS.get((unit or "").lower())
    c = row["content_per_sales_unit"]
    if not quantity or quantity <= 0 or want is None or not c or row["content_unit"] != want:
        return (0, 0.0)
    cost = math.log(math.ceil(quantity / c))
    if c > quantity:
        cost += math.log(c / quantity)
    return (1, -cost)


def _rank(row: sqlite3.Row, terms: list[str], quantity=None, unit=None):
    names = " ".join(row[c] or "" for c in ("name_fi", "name_en", "name_sv"))
    everything = " ".join(row[c] or "" for c in store.FTS_COLUMNS)
    return (_hits(names, terms), _hits(everything, terms), _fit(row, quantity, unit), -row["bm25"])


def search(conn: sqlite3.Connection, q: str = "", claims: str = "", exclude_allergens: str = "",
           category: str = "", limit: int = 20,
           quantity: float | None = None, unit: str | None = None) -> list[CatalogItem]:
    limit = max(1, min(int(limit), MAX_LIMIT))
    terms = store.fts_terms(q)
    rows = store.search_rows(conn, terms, _csv(claims), _csv(exclude_allergens),
                             category.strip(), CANDIDATES)
    if terms:
        rows = sorted(rows, key=lambda r: _rank(r, terms, quantity, unit), reverse=True)
    rows = rows[:limit]
    det = store.item_details(conn, [r["id"] for r in rows])
    return [CatalogItem(product_id=r["id"], name=r["name_en"] or r["name_fi"] or r["id"],
                        vendor_name=r["vendor_name"], sales_unit=r["sales_unit"],
                        category=r["category"], claims=det[r["id"]]["claims"],
                        allergens_contains=det[r["id"]]["contains"],
                        allergens_may_contain=det[r["id"]]["may"],
                        allergen_data=bool(r["has_allergen_data"])) for r in rows]
