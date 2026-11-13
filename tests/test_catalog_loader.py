"""T1 catalog loader: unit tests on 3 sample products + full-data checks (skipped without data/)."""
import json
from pathlib import Path

import pytest

from app.catalog.loader import DATA, load, load_from_files, normalize
from app.catalog.store import connect

SAMPLES = json.loads((Path(__file__).parent / "fixtures" / "sample_products.json").read_text())
RELIABILITY = {"460025": 0.996}
CO2 = {"463038": 1.1, "460000": 2.315, "460025": 0.825}


@pytest.fixture
def conn():
    c = connect(":memory:")
    load(c, SAMPLES, RELIABILITY, CO2)
    return c


def by_id(pid):
    return normalize(next(p for p in SAMPLES if p["id"] == pid), RELIABILITY, CO2)


def test_eila_milk_normalized():
    r = by_id("463038")
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
    r = by_id("460000")
    assert r["product"]["vendor_name"] == "ATRIA SUOMI OY TUORE"
    assert ("AE", "CONTAINS") in r["allergens"]
    assert r["product"]["key_ingredients_en"].startswith("potato 56 %")
    assert r["substitutions"] == []


def test_courgette_has_substitute_but_no_allergen_data():
    r = by_id("460025")
    assert r["product"]["has_allergen_data"] == 0
    assert r["allergens"] == []
    assert r["product"]["delivery_reliability"] == 0.996
    assert r["substitutions"] == ["460150"]


def test_load_writes_all_tables(conn):
    assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 3
    claims = {r[0] for r in conn.execute("SELECT claim FROM product_claims WHERE product_id='463038'")}
    assert "FREE_FROM_LACTOSE" in claims
    # 460150 isn't in the sample set, so the dangling substitution is dropped
    assert conn.execute("SELECT COUNT(*) FROM substitutions").fetchone()[0] == 0


def test_reload_replaces_rows(conn):
    load(conn, SAMPLES[:1], RELIABILITY, CO2)
    assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 1


LACTOSE_FREE_MILK = """
SELECT COUNT(*) FROM products p JOIN product_claims c ON c.product_id = p.id
WHERE c.claim = 'FREE_FROM_LACTOSE'
  AND (lower(p.name_fi) LIKE '%maito%' OR lower(p.name_en) LIKE '%milk%')
"""


@pytest.mark.skipif(not (DATA / "products.json").exists(), reason="run scripts/download_data.py")
def test_full_catalog():
    c = connect(":memory:")
    assert load_from_files(c) == 17546
    assert 75 <= c.execute(LACTOSE_FREE_MILK).fetchone()[0] <= 100
    share = c.execute("SELECT AVG(has_allergen_data) FROM products").fetchone()[0]
    assert 0.50 <= share <= 0.58
    assert c.execute("SELECT COUNT(*) FROM substitutions").fetchone()[0] > 150
