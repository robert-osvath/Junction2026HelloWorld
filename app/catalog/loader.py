"""T1: load products.json + reliability/CO2 CSVs into the catalog DB, normalized.

Usage: uv run python -m app.catalog.loader      (reads data/, writes data/catalog.db)
"""
import csv
import json
import re
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


# --- pack content (L or KG per sales unit) ---
# unitConversions units are messy (the "volume" field often repeats the weight in GRM, or counts
# pieces as H87), so: a real volume unit wins, else a real weight unit, else parse the name.
_VOLUME = {"LTR": 1.0, "MLT": 0.001, "CLT": 0.01, "DLT": 0.1}
_WEIGHT = {"GRM": 0.001, "KGM": 1.0}
_NAME_UNITS = {"l": ("L", 1.0), "dl": ("L", 0.1), "cl": ("L", 0.01), "ml": ("L", 0.001),
               "kg": ("KG", 1.0), "g": ("KG", 0.001)}
_SIZE = re.compile(r"(?<![\w.,])(?:(\d+)\s*[x×]\s*)?(\d+(?:[.,]\d+)?)\s*(kg|ml|dl|cl|g|l)(?![\w])", re.I)
_LEADING_COUNT = re.compile(r"^\s*\d+\s*[x×]\s", re.I)


def parse_name_content(name: str | None) -> tuple[float, int, str] | None:
    """(size per item, count, 'L'|'KG') from a product name: "10 l" -> (10, 1), "2,5 dl", "400g",
    "100x2 cl" -> (0.02, 100). None when absent or ambiguous: several different sizes ("465ml/406g")
    or a leading "12 x ..." count whose scope is unclear."""
    if not name or _LEADING_COUNT.match(name):
        return None
    found = set()
    for m in _SIZE.finditer(name):
        if re.search(r"\d\s*/\s*$", name[:m.start(2)]):
            return None  # "1/20 L" is a fraction or a ratio, not a size
        count, num, unit = m.groups()
        kind, factor = _NAME_UNITS[unit.lower()]
        found.add((round(float(num.replace(",", ".")) * factor, 6), int(count or 1), kind))
    return found.pop() if len(found) == 1 else None


def _amount(m: dict | None, table: dict) -> float | None:
    if m and m.get("unit") in table and m.get("value"):
        return m["value"] * table[m["unit"]]
    return None


def content_per_sales_unit(raw: dict, names: list[str | None]) -> tuple[float | None, str | None, int]:
    """(content, 'L'|'KG', confirmed) per sales unit; (None, None, 0) when unknown. `confirmed` is 1
    when the product name states a size that agrees with the value (so it is a real pack size and not
    just a packaging figure from unitConversions)."""
    if raw["salesUnit"] == raw.get("baseUnit"):
        n_base = 1.0
    else:
        n_base = next((u.get("sizeInBaseUnits") for u in raw.get("units", [])
                       if u["unitId"] == raw["salesUnit"]), None)
    if not n_base:
        return None, None, 0
    uc = None  # content per base unit from unitConversions, (amount, kind)
    for u in raw.get("synkkaData", {}).get("unitConversions") or []:
        if u.get("synkkaPackagingType") == "BASE_UNIT_OR_EACH":
            if (v := _amount(u.get("volume"), _VOLUME)) is not None:
                uc = (v, "L")
            elif (w := _amount(u.get("netWeight"), _WEIGHT)) is not None:
                uc = (w, "KG")
    from_uc = (round(uc[0] * n_base, 6), uc[1]) if uc else None
    from_name = None
    for n in names:
        if (p := parse_name_content(n)) is not None:
            size, count, kind = p
            if count == 1:  # "10 l": one base unit holds this much
                from_name = (round(size * n_base, 6), kind)
            elif n_base in (1, count):  # "12x250 ml": the name describes the whole pack
                from_name = (round(size * count, 6), kind)
            break  # an unclear multipack stays unknown from the name
    # unitConversions can hold gross/shipping figures (a 500 ml bowl as 6 kg), so an unambiguous
    # name wins when the two disagree in dimension or by more than 25%.
    if from_uc and from_name and (from_uc[1] != from_name[1]
                                  or not 0.75 <= from_uc[0] / from_name[0] <= 1 / 0.75):
        return (*from_name, 1)
    best = from_uc or from_name
    return (*best, int(from_name is not None)) if best else (None, None, 0)


def normalize(raw: dict, reliability: dict[str, float], co2: dict[str, float]) -> dict:
    """One raw product -> {product: row, claims, allergens, labels, units, substitutions}."""
    synkka = raw.get("synkkaData", {})
    pid = raw["id"]
    allergens = _allergen_levels(synkka)
    names = synkka.get("names", [])
    ingredients = synkka.get("keyIngredients", [])
    content, content_unit, content_confirmed = content_per_sales_unit(raw, [_localized(names, "en"), _localized(names, "fi")])
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
            "content_per_sales_unit": content,
            "content_unit": content_unit,
            "content_confirmed": content_confirmed,
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
