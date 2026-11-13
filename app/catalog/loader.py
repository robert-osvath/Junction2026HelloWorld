"""T1: load products.json + reliability/CO2 CSVs into the catalog DB, normalized.

Usage: uv run python -m app.catalog.loader      (reads data/, writes data/catalog.db)
"""
import csv
import json
import sqlite3
import sys
from pathlib import Path

from app.catalog.store import TABLES, connect, db_path

DATA = Path(__file__).resolve().parents[2] / "data"


def _text(values: list[dict], lang: str) -> str | None:
    return next((v["value"] for v in values if v.get("language") == lang), None)


def _classification(synkka: dict, name: str) -> list[dict]:
    for c in synkka.get("classifications", []):
        if c["name"] == name:
            return c.get("values", [])
    return []


def normalize(raw: dict, reliability: dict[str, float], co2: dict[str, float]) -> dict:
    """One raw product -> {product: row, claims, allergens, labels, units, substitutions}."""
    s = raw.get("synkkaData", {})
    pid = raw["id"]
    allergens = [(v["id"], v["unit"]) for v in _classification(s, "allergen")]
    allergens += [(v["id"], "FREE_FROM") for v in _classification(s, "nonAllergen")]
    names = s.get("names", [])
    ingredients = s.get("keyIngredients", [])
    return {
        "product": {
            "id": pid,
            "name_fi": _text(names, "fi"),
            "name_en": _text(names, "en"),
            "name_sv": _text(names, "sv"),
            "brand": s.get("brand"),
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
            "key_ingredients_fi": _text(ingredients, "fi"),
            "key_ingredients_en": _text(ingredients, "en"),
        },
        "claims": sorted({v["id"] for v in _classification(s, "nutritionalClaim")}),
        "allergens": sorted(set(allergens)),
        "labels": sorted({v["id"] for v in _classification(s, "packagingMarkedLabel")}),
        "units": sorted({(u["unitId"], u.get("sizeInBaseUnits")) for u in raw.get("units", [])}),
        "substitutions": sorted({x["substituteProduct"] for x in raw.get("substitutions") or []
                                 if x.get("product") == pid}),
    }


def read_csv_map(path: Path, value_col: str) -> dict[str, float]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {row["productId"]: float(row[value_col]) for row in csv.DictReader(f)
                if row.get(value_col) not in (None, "")}


def load(conn: sqlite3.Connection, products: list[dict], reliability: dict[str, float],
         co2: dict[str, float]) -> int:
    """Replace the catalog with `products`. Skips deleted products. Returns rows loaded."""
    rows = [normalize(p, reliability, co2) for p in products if not p.get("deleted")]
    with conn:
        for t in TABLES:
            conn.execute(f"DELETE FROM {t}")
        cols = list(rows[0]["product"]) if rows else []
        conn.executemany(
            f"INSERT INTO products ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [tuple(r["product"][c] for c in cols) for r in rows])
        for r in rows:
            pid = r["product"]["id"]
            conn.executemany("INSERT INTO product_claims VALUES (?, ?)", [(pid, c) for c in r["claims"]])
            conn.executemany("INSERT INTO product_allergens VALUES (?, ?, ?)",
                             [(pid, code, level) for code, level in r["allergens"]])
            conn.executemany("INSERT INTO product_labels VALUES (?, ?)", [(pid, l) for l in r["labels"]])
            conn.executemany("INSERT INTO product_units VALUES (?, ?, ?)",
                             [(pid, u, size) for u, size in r["units"]])
        # substitutions last, and only between products we actually loaded
        loaded = {r["product"]["id"] for r in rows}
        conn.executemany("INSERT INTO substitutions VALUES (?, ?)",
                         [(r["product"]["id"], s) for r in rows for s in r["substitutions"] if s in loaded])
    return len(rows)


def load_from_files(conn: sqlite3.Connection, data_dir: Path = DATA) -> int:
    products = json.loads((data_dir / "products.json").read_text(encoding="utf-8"))
    return load(conn, products,
                read_csv_map(data_dir / "delivery_reliability.csv", "deliveryReliability"),
                read_csv_map(data_dir / "carbon_footprint.csv", "AVG kg CO2-eq/kg"))


def main() -> int:
    if not (DATA / "products.json").exists():
        sys.exit("data/products.json missing: run `uv run scripts/download_data.py` first")
    conn = connect()
    n = load_from_files(conn)
    print(f"loaded {n:,} products into {db_path()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
