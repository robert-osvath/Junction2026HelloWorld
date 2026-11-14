"""T2 catalog search: unit tests on the 3 sample products (conftest DB) + full-data checks."""
import json
import os
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.catalog import store
from app.catalog.loader import DATA_DIR
from app.catalog.search import search
from app.main import app
from contracts.models import CatalogItem

client = TestClient(app)
FIXTURE = json.loads((Path(__file__).parent.parent / "contracts/fixtures/catalog_item.json").read_text())
full_data = pytest.mark.skipif(not (DATA_DIR / "products.json").exists(), reason="needs data/products.json")


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """A per-test copy of the sample DB (tests add rows and the endpoint reads the same copy)."""
    copy = tmp_path / "catalog.db"
    shutil.copy(os.environ["CATALOG_DB"], copy)
    monkeypatch.setenv("CATALOG_DB", str(copy))
    c = store.connect()
    yield c
    c.close()


def ids(items):
    return [i.product_id for i in items]


def test_eila_maps_to_catalog_item(conn):
    item = search(conn, "laktoositon maito")[0]
    assert item.product_id == "463038"
    got = item.model_dump()
    for k in ("product_id", "name", "vendor_name", "sales_unit", "category", "allergens_contains",
              "allergens_may_contain", "allergen_data"):
        assert got[k] == FIXTURE[k]
    assert set(FIXTURE["claims"]) <= set(item.claims)


def test_english_and_compound_match(conn):
    assert ids(search(conn, "lactose-free milk")) == ["463038"]
    assert ids(search(conn, "maito")) == ["463038"]  # substring of kevytmaitojuoma
    assert ids(search(conn, "MAITOJUOMA")) == ["463038"]  # case-insensitive
    assert search(conn, "zzzznothing") == []


def test_ingredients_and_brand_indexed(conn):
    assert ids(search(conn, "vitamin"))[:1] == ["463038"]  # key_ingredients_en "MILK, vitamin D."
    assert ids(search(conn, "forssan")) == ["460000"]


def test_claims_all_required(conn):
    assert set(ids(search(conn, claims="FREE_FROM_LACTOSE"))) == {"463038", "460000"}
    assert ids(search(conn, claims="FREE_FROM_LACTOSE,LOW_FAT")) == ["463038"]
    assert search(conn, claims="FREE_FROM_LACTOSE,NO_SUCH_CLAIM") == []


def test_exclude_allergens(conn):
    # 463038 CONTAINS AM; 460000 has AM FREE_FROM but CONTAINS AE; 460025 has no allergen data
    assert set(ids(search(conn, exclude_allergens="AM"))) == {"460000", "460025"}
    assert set(ids(search(conn, exclude_allergens="AM,AE"))) == {"460025"}  # no data -> kept
    assert search(conn, exclude_allergens="AM,AE")[0].allergen_data is False


def test_exclude_allergens_may_contain(conn):
    conn.execute("INSERT INTO product_allergens VALUES ('460025', 'AW', 'MAY_CONTAIN')")
    assert "460025" not in ids(search(conn, exclude_allergens="AW"))
    assert "460025" in ids(search(conn, exclude_allergens="AM"))
    item = next(i for i in search(conn) if i.product_id == "460025")
    assert item.allergens_may_contain == ["AW"]


def test_category_and_filter_only_browse(conn):
    assert ids(search(conn, category="10400")) == ["463038"]
    assert search(conn, category="nope") == []
    assert len(search(conn)) == 3  # empty q, no filters: browse
    assert ids(search(conn, q="salaatti", category="10400")) == []


def test_limit_clamped(conn):
    assert len(search(conn, limit=2)) == 2
    assert len(search(conn, limit=0)) == 1
    assert len(search(conn, limit=-5)) == 1
    assert len(search(conn, limit=10_000)) == 3


def test_endpoint_returns_contract(conn):
    r = client.get("/catalog/search", params={"q": "laktoositon maito", "claims": "FREE_FROM_LACTOSE",
                                              "exclude_allergens": "AE", "limit": 5})
    assert r.status_code == 200
    items = [CatalogItem(**x) for x in r.json()]
    assert ids(items) == ["463038"]


def test_punctuation_in_query_is_safe(conn):
    for q in ['"', "milk OR", "a*b", "(", "-", "NEAR(", "lactose-free"]:
        search(conn, q)


def test_fts_rebuilds_on_existing_db(tmp_path):
    c = store.connect(tmp_path / "old.db")
    c.execute("INSERT INTO products (id, name_fi, sales_unit, has_allergen_data) VALUES ('1', 'Kaurajuoma', 'ST', 0)")
    c.commit()
    c.close()
    c = store.connect(tmp_path / "old.db")  # empty index + products -> rebuilt on connect
    assert ids(search(c, "kaura")) == ["1"]


def _add(conn, pid, name, content, unit="L", confirmed=1):
    conn.execute("INSERT INTO products (id, name_en, sales_unit, has_allergen_data, content_per_sales_unit, "
                 "content_unit, content_confirmed) VALUES (?, ?, 'KI', 0, ?, ?, ?)",
                 (pid, name, content, unit, confirmed))


def test_pack_fit_ordering(conn):
    for pid, content, unit in [("s", 1, "L"), ("m", 20, "L"), ("big", 1000, "L"), ("huge", 5000, "L"),
                               ("kg", 20, "KG"), ("unk", None, None)]:
        _add(conn, pid, "oatdrink " + pid, content, unit)
    store.rebuild_fts(conn)
    order = lambda **kw: ids(search(conn, "oatdrink", **kw))
    # 1000 L: exact fit first, then fewer units / less waste; other-unit and unknown last, never dropped
    assert order(quantity=1000, unit="L")[:4] == ["big", "huge", "m", "s"]
    assert set(order(quantity=1000, unit="L")[4:]) == {"kg", "unk"}
    assert order(quantity=15, unit="L")[:2] == ["m", "s"]  # 20 L beats 1000 L (waste) and 1 L (15 units)
    assert order(quantity=20, unit="kg")[0] == "kg"
    # no quantity, 'pcs' or non-positive quantity: fit ignored, order unchanged
    base = order()
    assert order(quantity=1000, unit="pcs") == base and order(quantity=1000) == base
    assert order(unit="L") == base and order(quantity=0, unit="L") == base


def test_endpoint_quantity_params(conn):
    r = client.get("/catalog/search", params={"q": "milk", "quantity": 1000, "unit": "L"})
    assert r.status_code == 200 and r.json()[0]["product_id"] == "463038"
    assert client.get("/catalog/search", params={"quantity": 5, "unit": "gallons"}).status_code == 422


def test_nan_inf_quantity_rejected(conn):
    for bad in ("nan", "inf", "-inf", "0", "-5"):
        r = client.get("/catalog/search", params={"q": "maito", "quantity": bad, "unit": "L"})
        assert r.status_code == 422, bad
    # the search function itself is also safe
    assert search(conn, "maito", quantity=float("nan"), unit="L")
    assert search(conn, "maito", quantity=float("inf"), unit="L")


def test_short_and_punctuation_queries(conn):
    _add(conn, "beer", "Lapin Kulta beer 0,33 l", 0.33)
    conn.execute("UPDATE products SET name_fi = 'Olut ja öl-sekoitus' WHERE id = 'beer'")
    store.rebuild_fts(conn)
    assert ids(search(conn, "öl")) == ["beer"]   # short token: prefix match on name words
    assert ids(search(conn, "ÖL")) == ["beer"]   # case-insensitive
    assert ids(search(conn, "zz")) == []         # not an unfiltered browse
    assert search(conn, '"') == [] and search(conn, "!!") == []
    assert len(search(conn, "")) == 4 and len(search(conn, "   ")) == 4  # only an empty q browses
    assert ids(search(conn, "ol", claims="FREE_FROM_LACTOSE")) == []  # filters still apply
    assert ids(search(conn, "öl maito")) == ["463038"]  # long token via FTS, short one ignored


def test_relevance_beats_pack_fit(conn):
    _add(conn, "scoop", "Ice cream scoop 1/20", 100, confirmed=0)
    _add(conn, "cream", "Cooking cream 15 l cream", 15)
    store.rebuild_fts(conn)
    assert ids(search(conn, "cream", quantity=100, unit="L"))[0] == "cream"


# --- full catalog (built into a temp DB, never touches data/catalog.db) ---

@pytest.fixture(scope="module")
def full_db(tmp_path_factory):
    from app.catalog.loader import load_from_files
    path = tmp_path_factory.mktemp("full") / "catalog.db"
    c = store.connect(path)
    load_from_files(c)
    c.close()
    mp = pytest.MonkeyPatch()
    mp.setenv("CATALOG_DB", str(path))
    yield path
    mp.undo()


@full_data
@pytest.mark.parametrize("q", ["laktoositon maito", "lactose-free milk"])
def test_acceptance_eila_found(q, full_db):
    r = client.get("/catalog/search", params={"q": q, "limit": 100}).json()
    assert "463038" in [x["product_id"] for x in r]


@full_data
@pytest.mark.parametrize("q", ["laktoositon maito", "lactose-free milk"])
def test_acceptance_eila_top5_for_1000_l(q, full_db):
    r = client.get("/catalog/search", params={"q": q, "limit": 5, "quantity": 1000, "unit": "L"}).json()
    assert "463038" in [x["product_id"] for x in r]


@full_data
@pytest.mark.parametrize("q", ["laktoositon maito", "lactose-free milk"])
def test_search_under_200ms(q, full_db):
    client.get("/catalog/search", params={"q": q, "quantity": 1000, "unit": "L"})  # warm-up
    t = time.perf_counter()
    assert client.get("/catalog/search", params={"q": q, "quantity": 1000, "unit": "L"}).status_code == 200
    assert (time.perf_counter() - t) * 1000 < 200


@full_data
def test_cream_50_l_returns_cream_product_first(full_db):
    r = client.get("/catalog/search", params={"q": "cream", "limit": 3, "quantity": 50, "unit": "L"}).json()
    assert "scoop" not in r[0]["name"].lower() and "cream" in r[0]["name"].lower()


@full_data
def test_pack_content_never_contradicts_name(full_db):
    c = store.connect()
    rows = c.execute("SELECT id, name_en, content_per_sales_unit AS c, content_unit AS u "
                     "FROM products WHERE content_confirmed = 1").fetchall()
    assert rows
    assert c.execute("SELECT content_per_sales_unit, content_unit FROM products WHERE id = '463038'"
                     ).fetchone()[:] == (20.0, "L")
