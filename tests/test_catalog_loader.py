"""T1 catalog loader: unit tests on 3 sample products + full-data checks (skipped without data/)."""
import copy
import json
from pathlib import Path

import pytest

from app.catalog.loader import DATA_DIR, load, load_from_files, normalize, read_csv_map
from app.catalog.store import PRODUCT_COLUMNS, connect

SAMPLES = json.loads((Path(__file__).parent / "fixtures" / "sample_products.json").read_text())
RELIABILITY = {"460025": 0.996}
CO2 = {"463038": 1.1, "460000": 2.315, "460025": 0.825}


@pytest.fixture
def conn():
    c = connect(":memory:")
    load(c, SAMPLES, RELIABILITY, CO2)
    return c


def sample(pid):
    return copy.deepcopy(next(p for p in SAMPLES if p["id"] == pid))


def with_allergens(raw, allergen=(), non_allergen=()):
    """Replace a raw product's allergen / nonAllergen classifications."""
    for c in raw["synkkaData"]["classifications"]:
        if c["name"] == "allergen":
            c["values"] = [{"id": code, "unit": level} for code, level in allergen]
        if c["name"] == "nonAllergen":
            c["values"] = [{"id": code, "unit": "FREE_FROM"} for code in non_allergen]
    return raw


# --- the 3 sample products ---

def test_eila_milk_normalized():
    r = normalize(sample("463038"), RELIABILITY, CO2)
    p = r["product"]
    assert p["name_fi"] == "Valio Eila® kevytmaitojuoma 10 l novobox laktoositon"
    assert p["name_en"].startswith("Valio Eila® semi skimmed milk drink 10 l")
    assert p["sales_unit"] == "KI" and p["allowed_lot_size"] == 0.01
    assert p["has_allergen_data"] == 1
    assert p["delivery_reliability"] is None and p["co2_kg_per_kg"] == 1.1
    assert "FREE_FROM_LACTOSE" in r["claims"]
    assert ("AM", "CONTAINS") in r["allergens"]
    assert ("KI", 2.0) in r["units"]


def test_potato_salad_contains_egg():
    r = normalize(sample("460000"), RELIABILITY, CO2)
    assert r["product"]["vendor_name"] == "ATRIA SUOMI OY TUORE"
    assert ("AE", "CONTAINS") in r["allergens"]
    assert r["product"]["key_ingredients_en"].startswith("potato 56 %")
    assert r["substitutions"] == []


def test_courgette_has_substitute_but_no_allergen_data():
    r = normalize(sample("460025"), RELIABILITY, CO2)
    assert r["product"]["has_allergen_data"] == 0
    assert r["allergens"] == []
    assert r["product"]["delivery_reliability"] == 0.996
    assert r["substitutions"] == ["460150"]


def test_normalize_produces_exactly_the_product_columns():
    assert set(normalize(sample("463038"), {}, {})["product"]) == set(PRODUCT_COLUMNS)


# --- edge cases seen in the real data ---

def test_undeclared_allergens_dropped_and_not_counted_as_data():
    raw = with_allergens(sample("460025"), allergen=[("AM", "UNDECLARED")])
    r = normalize(raw, {}, {})
    assert r["allergens"] == [] and r["product"]["has_allergen_data"] == 0


def test_conflicting_levels_keep_the_most_cautious():
    # real case 468044: AM listed as CONTAINS and FREE_FROM; 469429: MAY_CONTAIN and FREE_FROM
    raw = with_allergens(sample("460025"), allergen=[("AM", "CONTAINS"), ("AN", "MAY_CONTAIN")],
                         non_allergen=["AM", "AN", "AY"])
    assert normalize(raw, {}, {})["allergens"] == [("AM", "CONTAINS"), ("AN", "MAY_CONTAIN"),
                                                   ("AY", "FREE_FROM")]


def test_self_substitution_dropped():
    raw = sample("460025")
    raw["substitutions"].append({"product": "460025", "substituteProduct": "460025"})
    assert normalize(raw, {}, {})["substitutions"] == ["460150"]


def test_read_csv_map_skips_blanks_and_missing_file(tmp_path):
    f = tmp_path / "r.csv"
    f.write_text("productId,deliveryReliability\n1,0.5\n2,\n3, \n")
    assert read_csv_map(f, "deliveryReliability") == {"1": 0.5}
    assert read_csv_map(tmp_path / "nope.csv", "deliveryReliability") == {}


# --- loading into the DB ---

def test_load_writes_all_tables(conn):
    assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 3
    claims = {r[0] for r in conn.execute("SELECT claim FROM product_claims WHERE product_id='463038'")}
    assert "FREE_FROM_LACTOSE" in claims
    # 460150 isn't in the sample set, so the dangling substitution is dropped
    assert conn.execute("SELECT COUNT(*) FROM substitutions").fetchone()[0] == 0


def test_load_skips_deleted_and_replaces_rows(conn):
    deleted = sample("460000")
    deleted["deleted"] = True
    assert load(conn, [sample("463038"), deleted], {}, {}) == 1
    assert [r[0] for r in conn.execute("SELECT id FROM products")] == ["463038"]


# --- full real catalog (needs `uv run scripts/download_data.py`) ---

LACTOSE_FREE_MILK = """
SELECT COUNT(*) FROM products p
WHERE EXISTS (SELECT 1 FROM product_claims c WHERE c.product_id = p.id AND c.claim = 'FREE_FROM_LACTOSE')
  AND EXISTS (SELECT 1 FROM product_allergens a
              WHERE a.product_id = p.id AND a.code = 'AM' AND a.level = 'CONTAINS')
  AND (lower(p.name_fi) LIKE '%maito%' OR lower(p.name_en) LIKE '%milk%')
"""


@pytest.mark.skipif(not (DATA_DIR / "products.json").exists(), reason="run scripts/download_data.py")
def test_full_catalog():
    c = connect(":memory:")
    assert load_from_files(c) == 17546
    # spec: "~85 lactose-free milk products". This is a coarse load check, not a milk definition:
    # the name match also catches milk chocolate, coffee drinks, quark. Precise matching is T2's job.
    assert 70 <= c.execute(LACTOSE_FREE_MILK).fetchone()[0] <= 100
    share = c.execute("SELECT AVG(has_allergen_data) FROM products").fetchone()[0]
    assert 0.52 <= share <= 0.56  # team note: allergen data on ~54%
    levels = {r[0] for r in c.execute("SELECT DISTINCT level FROM product_allergens")}
    assert levels == {"CONTAINS", "MAY_CONTAIN", "FREE_FROM"}
    assert c.execute("SELECT COUNT(*) FROM substitutions WHERE product_id = substitute_id").fetchone()[0] == 0
    eila = c.execute("SELECT level FROM product_allergens WHERE product_id='463038' AND code='AM'").fetchone()
    assert eila[0] == "CONTAINS"
