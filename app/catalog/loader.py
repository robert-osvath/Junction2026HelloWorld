"""T1: load products.json + reliability/CO2 CSVs into the catalog DB, normalized.

Usage: uv run python -m app.catalog.loader      (reads data/, writes data/catalog.db)
"""
import csv
import json
import sqlite3
import sys
from pathlib import Path

from app.catalog.store import connect, db_path, replace_catalog

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# Allergen levels we store. The raw data also has 303 "UNDECLARED" entries, which carry no
# information, so they are dropped and don't count as allergen data.
CONTAINS, MAY_CONTAIN, FREE_FROM = "CONTAINS", "MAY_CONTAIN", "FREE_FROM"
ALLERGEN_LEVELS = (CONTAINS, MAY_CONTAIN, FREE_FROM)
# When the raw data lists one code at two levels (e.g. AM both CONTAINS and FREE_FROM, seen on
# 468044 and 469429), keep the most cautious one so a FREE_FROM filter never admits it.
_CAUTION = {CONTAINS: 0, MAY_CONTAIN: 1, FREE_FROM: 2}


def _localized(values: list[dict], lang: str) -> str | None:
    return next((v["value"] for v in values if v.get("language") == lang), None)


def _classification(synkka: dict, name: str) -> list[dict]:
    for c in synkka.get("classifications", []):
        if c["name"] == name:
            return c.get("values", [])
    return []


def _allergen_levels(synkka: dict) -> dict[str, str]:
    """GS1 code -> single level, most cautious wins, UNDECLARED dropped."""
    declared = [(v["id"], v.get("unit")) for v in _classification(synkka, "allergen")]
    declared += [(v["id"], FREE_FROM) for v in _classification(synkka, "nonAllergen")]
    levels: dict[str, str] = {}
    for code, level in declared:
        if level not in ALLERGEN_LEVELS:
            continue
        if code not in levels or _CAUTION[level] < _CAUTION[levels[code]]:
            levels[code] = level
    return levels


def normalize(raw: dict, reliability: dict[str, float], co2: dict[str, float]) -> dict:
    """One raw product -> {product: row, claims, allergens, labels, units, substitutions}."""
    synkka = raw.get("synkkaData", {})
    pid = raw["id"]
    allergens = _allergen_levels(synkka)
    names = synkka.get("names", [])
    ingredients = synkka.get("keyIngredients", [])
    return {
        "product": {
            "id": pid,
            "name_fi": _localized(names, "fi"),
            "name_en": _localized(names, "en"),
            "name_sv": _localized(names, "sv"),
            "brand": synkka.get("brand"),
            "category": raw.get("category"),
            "vendor_name": raw.get("vendorName"),
            "sales_unit": raw["salesUnit"],
            "base_unit": raw.get("baseUnit"),
            "allowed_lot_size": raw.get("allowedLotSize"),
            "country_of_origin": raw.get("countryOfOrigin"),
            "temperature_condition": raw.get("temperatureCondition"),
            "has_allergen_data": int(bool(allergens)),
            "delivery_reliability": reliability.get(pid),
            "co2_kg_per_kg": co2.get(pid),
            "key_ingredients_fi": _localized(ingredients, "fi"),
            "key_ingredients_en": _localized(ingredients, "en"),
        },
        "claims": sorted({v["id"] for v in _classification(synkka, "nutritionalClaim")}),
        "allergens": sorted(allergens.items()),
        "labels": sorted({v["id"] for v in _classification(synkka, "packagingMarkedLabel")}),
        "units": sorted({(u["unitId"], u.get("sizeInBaseUnits")) for u in raw.get("units", [])}),
        "substitutions": sorted({x["substituteProduct"] for x in raw.get("substitutions") or []
                                 if x["substituteProduct"] != pid}),
    }


def read_csv_map(path: Path, value_col: str) -> dict[str, float]:
    """productId -> float from a CSV; blank values skipped, missing file -> {}."""
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {row["productId"]: float(row[value_col]) for row in csv.DictReader(f)
                if (row.get(value_col) or "").strip()}


def load(conn: sqlite3.Connection, products: list[dict], reliability: dict[str, float],
         co2: dict[str, float]) -> int:
    """Replace the catalog with `products`. Skips deleted products. Returns rows loaded."""
    rows = [normalize(p, reliability, co2) for p in products if not p.get("deleted")]
    loaded = {r["product"]["id"] for r in rows}
    replace_catalog(
        conn,
        products=[r["product"] for r in rows],
        claims=[(r["product"]["id"], c) for r in rows for c in r["claims"]],
        allergens=[(r["product"]["id"], code, level) for r in rows for code, level in r["allergens"]],
        labels=[(r["product"]["id"], label) for r in rows for label in r["labels"]],
        units=[(r["product"]["id"], unit, size) for r in rows for unit, size in r["units"]],
        # only between products we actually loaded
        substitutions=[(r["product"]["id"], s) for r in rows for s in r["substitutions"] if s in loaded],
    )
    return len(rows)


def load_from_files(conn: sqlite3.Connection, data_dir: Path = DATA_DIR) -> int:
    products = json.loads((data_dir / "products.json").read_text(encoding="utf-8"))
    return load(conn, products,
                read_csv_map(data_dir / "delivery_reliability.csv", "deliveryReliability"),
                read_csv_map(data_dir / "carbon_footprint.csv", "AVG kg CO2-eq/kg"))


def main() -> int:
    if not (DATA_DIR / "products.json").exists():
        sys.exit("data/products.json missing: run `uv run scripts/download_data.py` first")
    conn = connect()
    n = load_from_files(conn)
    print(f"loaded {n:,} products into {db_path()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
