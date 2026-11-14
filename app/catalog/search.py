"""Catalog search: full-text candidates from store.py, reranked here, mapped to CatalogItem.

Ranking (tuple, higher is better), all generic:
1. how many query terms occur in the product NAME (fi/en/sv), then anywhere indexed;
2. bm25 relevance (name columns weighted far above ingredients, see store.FTS_WEIGHTS).
A vector score can later be blended in as an extra key of `_rank` without changing the signature.
"""
import sqlite3

from app.catalog import store
from contracts import CatalogItem

CANDIDATES = 300
MAX_LIMIT = 100


def _csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def _hits(text: str, terms: list[str]) -> int:
    text = text.lower()
    return sum(t in text for t in terms)


def _rank(row: sqlite3.Row, terms: list[str]):
    names = " ".join(row[c] or "" for c in ("name_fi", "name_en", "name_sv"))
    everything = " ".join(row[c] or "" for c in store.FTS_COLUMNS)
    return (_hits(names, terms), _hits(everything, terms), -row["bm25"])


def search(conn: sqlite3.Connection, q: str = "", claims: str = "", exclude_allergens: str = "",
           category: str = "", limit: int = 20) -> list[CatalogItem]:
    limit = max(1, min(int(limit), MAX_LIMIT))
    terms = store.fts_terms(q)
    rows = store.search_rows(conn, terms, _csv(claims), _csv(exclude_allergens),
                             category.strip(), CANDIDATES)
    if terms:
        rows = sorted(rows, key=lambda r: _rank(r, terms), reverse=True)
    rows = rows[:limit]
    det = store.item_details(conn, [r["id"] for r in rows])
    return [CatalogItem(product_id=r["id"], name=r["name_en"] or r["name_fi"] or r["id"],
                        vendor_name=r["vendor_name"], sales_unit=r["sales_unit"],
                        category=r["category"], claims=det[r["id"]]["claims"],
                        allergens_contains=det[r["id"]]["contains"],
                        allergens_may_contain=det[r["id"]]["may"],
                        allergen_data=bool(r["has_allergen_data"])) for r in rows]
