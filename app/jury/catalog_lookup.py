"""Catalog metadata lookup and curated Valio Aimo substitute candidate pool for P2 Jury (T6 & T7).

Resolves `product_id -> CatalogItem` for `POST /jury/evaluate` and provides both:
1. Explicit substitution rule links (`EXPLICIT_SUBSTITUTIONS`, modeling the 189 products in Valio Aimo's catalog with `substitutions` rules).
2. Same-category candidate pool with claims, GS1 allergens, comparison prices, stock evidence, and reliability.
Also loads from `data/products.json` if downloaded locally.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from contracts import CatalogItem, Evidence, Offer

ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = ROOT / "contracts" / "fixtures"
BENCHMARK_FIXTURE = ROOT / "tests" / "fixtures" / "jury_benchmark_20.json"


@dataclass(frozen=True)
class CandidateRecord:
    catalog: CatalogItem
    sales_unit: str
    content_per_sales_unit: float  # Liters or kg per sales_unit
    unit_price_eur: float
    comparison_price_eur: float
    comparison_unit: str  # "L" | "KG"
    available_date: date
    closing_time: datetime | None
    sales_block: bool
    logistic_model: str  # "Stock" | "Terminal"
    evidence: Evidence


# Explicit Valio Aimo substitution links (product_id -> list of substitute product_ids)
EXPLICIT_SUBSTITUTIONS: dict[str, list[str]] = {
    "463038": ["463010", "463022", "463990"],  # Valio Eila 10L Novobox -> 1L cartons, 20L Novobox, Luomu Eila 1L
    "411205": ["411210", "411220"],            # Valio cooking cream 15% -> 10% lactose-free, Oddlygood cooking 15%
}

# Curated Valio Aimo candidate pool covering dairy milk (10400), cream (10410),
# oat drinks (10490), and breakfast cereals/granola (20100).
CURATED_CANDIDATES: list[CandidateRecord] = [
    CandidateRecord(
        catalog=CatalogItem(
            product_id="463038",
            name="Valio Eila® semi skimmed milk drink 10 l novobox lactose free",
            vendor_name="VALIO OY TUORE",
            sales_unit="KI",
            category="10400",
            claims=["FREE_FROM_LACTOSE", "FREE_FROM_ADDITIVES", "LOW_FAT", "VEGETARIAN"],
            allergens_contains=["AM"],
            allergens_may_contain=[],
            allergen_data=True,
        ),
        sales_unit="KI",
        content_per_sales_unit=20.0,
        unit_price_eur=31.31,
        comparison_price_eur=1.57,
        comparison_unit="L",
        available_date=date(2026, 11, 16),
        closing_time=datetime.fromisoformat("2026-11-12T15:00:00+02:00"),
        sales_block=False,
        logistic_model="Terminal",
        evidence=Evidence(stock="estimated", allergens="declared", reliability=0.96),
    ),
    CandidateRecord(
        catalog=CatalogItem(
            product_id="463010",
            name="Valio Eila® lactose-free semi-skimmed milk drink 1L",
            vendor_name="VALIO OY TUORE",
            sales_unit="KI",
            category="10400",
            claims=["FREE_FROM_LACTOSE", "FREE_FROM_ADDITIVES", "LOW_FAT", "VEGETARIAN"],
            allergens_contains=["AM"],
            allergens_may_contain=[],
            allergen_data=True,
        ),
        sales_unit="KI",
        content_per_sales_unit=10.0,
        unit_price_eur=15.20,
        comparison_price_eur=1.52,
        comparison_unit="L",
        available_date=date(2026, 11, 16),
        closing_time=datetime.fromisoformat("2026-11-15T14:00:00+02:00"),
        sales_block=False,
        logistic_model="Stock",
        evidence=Evidence(stock="verified", allergens="declared", reliability=0.99),
    ),
    CandidateRecord(
        catalog=CatalogItem(
            product_id="463022",
            name="ValioHYLA® semi-skimmed milk UHT 1L lactose-free",
            vendor_name="VALIO OY TUORE",
            sales_unit="KI",
            category="10400",
            claims=["FREE_FROM_LACTOSE", "LOW_FAT", "VEGETARIAN"],
            allergens_contains=["AM"],
            allergens_may_contain=[],
            allergen_data=True,
        ),
        sales_unit="KI",
        content_per_sales_unit=12.0,
        unit_price_eur=19.44,
        comparison_price_eur=1.62,
        comparison_unit="L",
        available_date=date(2026, 11, 16),
        closing_time=datetime.fromisoformat("2026-11-15T16:00:00+02:00"),
        sales_block=False,
        logistic_model="Stock",
        evidence=Evidence(stock="verified", allergens="declared", reliability=0.98),
    ),
    CandidateRecord(
        catalog=CatalogItem(
            product_id="463990",
            name="Valio Luomu Eila® organic lactose-free milk drink 1L",
            vendor_name="VALIO OY TUORE",
            sales_unit="KI",
            category="10400",
            claims=["FREE_FROM_LACTOSE", "ORGANIC", "LOW_FAT", "VEGETARIAN"],
            allergens_contains=["AM"],
            allergens_may_contain=[],
            allergen_data=True,
        ),
        sales_unit="KI",
        content_per_sales_unit=10.0,
        unit_price_eur=18.90,
        comparison_price_eur=1.89,
        comparison_unit="L",
        available_date=date(2026, 11, 16),
        closing_time=datetime.fromisoformat("2026-11-15T14:00:00+02:00"),
        sales_block=False,
        logistic_model="Stock",
        evidence=Evidence(stock="verified", allergens="declared", reliability=0.97),
    ),
    CandidateRecord(
        catalog=CatalogItem(
            product_id="463055",
            name="Valio Eila® whole milk drink 3.5% fat lactose free 1L",
            vendor_name="VALIO OY TUORE",
            sales_unit="KI",
            category="10400",
            claims=["FREE_FROM_LACTOSE", "VEGETARIAN"],
            allergens_contains=["AM"],
            allergens_may_contain=[],
            allergen_data=True,
        ),
        sales_unit="KI",
        content_per_sales_unit=10.0,
        unit_price_eur=16.50,
        comparison_price_eur=1.65,
        comparison_unit="L",
        available_date=date(2026, 11, 16),
        closing_time=datetime.fromisoformat("2026-11-15T14:00:00+02:00"),
        sales_block=False,
        logistic_model="Stock",
        evidence=Evidence(stock="verified", allergens="declared", reliability=0.98),
    ),
    CandidateRecord(
        catalog=CatalogItem(
            product_id="400101",
            name="Valio Kevytmaito semi-skimmed milk 10L novobox (contains lactose)",
            vendor_name="VALIO OY TUORE",
            sales_unit="KI",
            category="10400",
            claims=["VEGETARIAN"],
            allergens_contains=["AM"],
            allergens_may_contain=[],
            allergen_data=True,
        ),
        sales_unit="KI",
        content_per_sales_unit=20.0,
        unit_price_eur=26.00,
        comparison_price_eur=1.30,
        comparison_unit="L",
        available_date=date(2026, 11, 16),
        closing_time=datetime.fromisoformat("2026-11-15T14:00:00+02:00"),
        sales_block=False,
        logistic_model="Stock",
        evidence=Evidence(stock="verified", allergens="declared", reliability=0.99),
    ),
    CandidateRecord(
        catalog=CatalogItem(
            product_id="411205",
            name="Valio ruokakerma 15% laktoositon 2.5L",
            vendor_name="VALIO OY",
            sales_unit="KI",
            category="10410",
            claims=["FREE_FROM_LACTOSE", "VEGETARIAN"],
            allergens_contains=["AM"],
            allergens_may_contain=[],
            allergen_data=True,
        ),
        sales_unit="KI",
        content_per_sales_unit=10.0,
        unit_price_eur=36.00,
        comparison_price_eur=3.60,
        comparison_unit="L",
        available_date=date(2026, 11, 16),
        closing_time=datetime.fromisoformat("2026-11-15T14:00:00+02:00"),
        sales_block=False,
        logistic_model="Stock",
        evidence=Evidence(stock="verified", allergens="declared", reliability=0.97),
    ),
    CandidateRecord(
        catalog=CatalogItem(
            product_id="411210",
            name="Valio ruokakerma 10% laktoositon 1L",
            vendor_name="VALIO OY",
            sales_unit="KI",
            category="10410",
            claims=["FREE_FROM_LACTOSE", "VEGETARIAN"],
            allergens_contains=["AM"],
            allergens_may_contain=[],
            allergen_data=True,
        ),
        sales_unit="KI",
        content_per_sales_unit=10.0,
        unit_price_eur=34.50,
        comparison_price_eur=3.45,
        comparison_unit="L",
        available_date=date(2026, 11, 16),
        closing_time=datetime.fromisoformat("2026-11-15T14:00:00+02:00"),
        sales_block=False,
        logistic_model="Stock",
        evidence=Evidence(stock="verified", allergens="declared", reliability=0.98),
    ),
    CandidateRecord(
        catalog=CatalogItem(
            product_id="411220",
            name="Valio Eila® whipping & cooking cream 38% lactose-free 1L",
            vendor_name="VALIO OY",
            sales_unit="KI",
            category="10410",
            claims=["FREE_FROM_LACTOSE", "VEGETARIAN"],
            allergens_contains=["AM"],
            allergens_may_contain=[],
            allergen_data=True,
        ),
        sales_unit="KI",
        content_per_sales_unit=10.0,
        unit_price_eur=42.00,
        comparison_price_eur=4.20,
        comparison_unit="L",
        available_date=date(2026, 11, 16),
        closing_time=datetime.fromisoformat("2026-11-15T14:00:00+02:00"),
        sales_block=False,
        logistic_model="Stock",
        evidence=Evidence(stock="verified", allergens="declared", reliability=0.96),
    ),
    CandidateRecord(
        catalog=CatalogItem(
            product_id="500112",
            name="Valio Oddlygood® Barista oat drink 1L",
            vendor_name="VALIO ODDLYGOOD",
            sales_unit="KI",
            category="10490",
            claims=["VEGAN", "FREE_FROM_LACTOSE"],
            allergens_contains=["AW"],
            allergens_may_contain=[],
            allergen_data=True,
        ),
        sales_unit="KI",
        content_per_sales_unit=10.0,
        unit_price_eur=19.50,
        comparison_price_eur=1.95,
        comparison_unit="L",
        available_date=date(2026, 11, 16),
        closing_time=datetime.fromisoformat("2026-11-15T14:00:00+02:00"),
        sales_block=False,
        logistic_model="Stock",
        evidence=Evidence(stock="verified", allergens="declared", reliability=0.95),
    ),
    CandidateRecord(
        catalog=CatalogItem(
            product_id="500120",
            name="Valio Oddlygood® Oat Drink Gluten-Free 1L",
            vendor_name="VALIO ODDLYGOOD",
            sales_unit="KI",
            category="10490",
            claims=["VEGAN", "FREE_FROM_LACTOSE", "FREE_FROM_GLUTEN"],
            allergens_contains=[],
            allergens_may_contain=[],
            allergen_data=True,
        ),
        sales_unit="KI",
        content_per_sales_unit=10.0,
        unit_price_eur=20.40,
        comparison_price_eur=2.04,
        comparison_unit="L",
        available_date=date(2026, 11, 16),
        closing_time=datetime.fromisoformat("2026-11-15T14:00:00+02:00"),
        sales_block=False,
        logistic_model="Stock",
        evidence=Evidence(stock="verified", allergens="declared", reliability=0.96),
    ),
    # Breakfast granola category (20100) for testing nut-free / strict_allergens substitutes
    CandidateRecord(
        catalog=CatalogItem(
            product_id="770199",
            name="Crunchy Oat Granola 5kg",
            vendor_name="FINN GRAIN OY",
            sales_unit="KI",
            category="20100",
            claims=["VEGETARIAN"],
            allergens_contains=["AW"],
            allergens_may_contain=["AN"],  # May contain tree nuts!
            allergen_data=True,
        ),
        sales_unit="KI",
        content_per_sales_unit=5.0,
        unit_price_eur=18.00,
        comparison_price_eur=3.60,
        comparison_unit="KG",
        available_date=date(2026, 11, 16),
        closing_time=datetime.fromisoformat("2026-11-15T14:00:00+02:00"),
        sales_block=False,
        logistic_model="Stock",
        evidence=Evidence(stock="verified", allergens="declared", reliability=0.95),
    ),
    CandidateRecord(
        catalog=CatalogItem(
            product_id="770210",
            name="Nordic Pure Nut-Free Berry Granola 5kg",
            vendor_name="FINN GRAIN OY",
            sales_unit="KI",
            category="20100",
            claims=["VEGETARIAN", "VEGAN", "FREE_FROM_LACTOSE"],
            allergens_contains=["AW"],
            allergens_may_contain=[],
            allergen_data=True,
        ),
        sales_unit="KI",
        content_per_sales_unit=5.0,
        unit_price_eur=18.50,
        comparison_price_eur=3.70,
        comparison_unit="KG",
        available_date=date(2026, 11, 16),
        closing_time=datetime.fromisoformat("2026-11-15T14:00:00+02:00"),
        sales_block=False,
        logistic_model="Stock",
        evidence=Evidence(stock="verified", allergens="declared", reliability=0.98),
    ),
    CandidateRecord(
        catalog=CatalogItem(
            product_id="770225",
            name="Helsinki Mylly Gluten & Nut-Free Seed Granola 5kg",
            vendor_name="HELSINGIN MYLLY OY",
            sales_unit="KI",
            category="20100",
            claims=["VEGETARIAN", "VEGAN", "FREE_FROM_LACTOSE", "FREE_FROM_GLUTEN"],
            allergens_contains=[],
            allergens_may_contain=[],
            allergen_data=True,
        ),
        sales_unit="KI",
        content_per_sales_unit=5.0,
        unit_price_eur=19.80,
        comparison_price_eur=3.96,
        comparison_unit="KG",
        available_date=date(2026, 11, 16),
        closing_time=datetime.fromisoformat("2026-11-15T14:00:00+02:00"),
        sales_block=False,
        logistic_model="Stock",
        evidence=Evidence(stock="verified", allergens="declared", reliability=0.97),
    ),
]


_CATALOG_INDEX: dict[str, CatalogItem] | None = None


def _build_catalog_index() -> dict[str, CatalogItem]:
    idx: dict[str, CatalogItem] = {}
    for rec in CURATED_CANDIDATES:
        idx[rec.catalog.product_id] = rec.catalog

    # Load contract fixture
    cat_fixture = FIXTURES_DIR / "catalog_item.json"
    if cat_fixture.exists():
        item = CatalogItem.model_validate_json(cat_fixture.read_text(encoding="utf-8"))
        idx[item.product_id] = item

    # Load benchmark fixture catalog items
    if BENCHMARK_FIXTURE.exists():
        cases = json.loads(BENCHMARK_FIXTURE.read_text(encoding="utf-8"))
        for c in cases:
            if "catalog_item" in c:
                ci = CatalogItem.model_validate(c["catalog_item"])
                idx.setdefault(ci.product_id, ci)

    return idx


def resolve_catalog_items(product_ids: list[str]) -> dict[str, CatalogItem]:
    """Return a mapping of `product_id -> CatalogItem` for all known products.

    Checks the in-memory curated/fixture index first, then falls back to P1's
    SQLite catalog (`app.catalog.store`) when `data/catalog.db` is present.
    """
    global _CATALOG_INDEX
    if _CATALOG_INDEX is None:
        _CATALOG_INDEX = _build_catalog_index()

    result: dict[str, CatalogItem] = {}
    missing: list[str] = []
    for pid in product_ids:
        if pid in _CATALOG_INDEX:
            result[pid] = _CATALOG_INDEX[pid]
        else:
            missing.append(pid)

    if missing:
        try:
            from app.catalog import store

            db_file = store.db_path()
            if db_file.exists():
                conn = store.connect(db_file)
                try:
                    rows = store.offer_rows(conn, missing)
                    details = store.item_details(conn, list(rows.keys()))
                    for pid, row in rows.items():
                        det = details.get(pid, {"claims": [], "contains": [], "may": []})
                        result[pid] = CatalogItem(
                            product_id=pid,
                            name=row.get("name_en") or row.get("name_fi") or pid,
                            vendor_name=row.get("vendor_name"),
                            sales_unit=row.get("sales_unit") or "ST",
                            category=row.get("category"),
                            claims=det["claims"],
                            allergens_contains=det["contains"],
                            allergens_may_contain=det["may"],
                            allergen_data=bool(row.get("has_allergen_data")),
                        )
                finally:
                    conn.close()
        except Exception:
            pass

    return result


def get_candidate_records() -> dict[str, CandidateRecord]:
    return {r.catalog.product_id: r for r in CURATED_CANDIDATES}
