"""Every mock endpoint answers and matches the contracts."""
import json

from fastapi.testclient import TestClient

from app.main import app
from app.mocks import FIXTURES, MODELS, load

c = TestClient(app)
F = {n: json.loads((FIXTURES / f"{n}.json").read_text()) for n in MODELS}


def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


def test_fixtures_validate():
    for name in MODELS:
        load(name)


def test_milk_fixture_math():
    o = load("offer")
    assert round(o.units_needed * o.unit_price_eur, 2) == o.total_eur
    # content per sales unit = catalogPrice / comparisonPrice ~ 20 L; 50 units covers 1,000 L
    assert o.units_needed * o.unit_price_eur / o.comparison_price_eur >= load("need").quantity * 0.99


def test_catalog_and_offers():
    assert ok(c.get("/catalog/search", params={"q": "laktoositon maito", "claims": "FREE_FROM_LACTOSE"}))
    assert ok(c.post("/offers", json={"need": F["need"], "product_ids": ["463038"], "delivery_date": "2026-11-16"}))


def test_jury_and_substitutes():
    body = {"mandate": F["mandate"], "need": F["need"], "offers": [F["offer"]]}
    assert ok(c.post("/jury/evaluate", json=body))["decision"] == "auto_order"
    assert ok(c.post("/substitutes", json={"mandate": F["mandate"], "need": F["need"], "offer": F["offer"]}))


def test_orders_and_audit():
    order = ok(c.post("/orders", json={"mandate_id": "mandate-hotel-tight", "need": F["need"], "lines": [F["offer"]]}))
    oid = order["id"]
    assert ok(c.get(f"/orders/{oid}"))["id"] == oid
    assert ok(c.post(f"/orders/{oid}/approve"))["status"] == "placed"
    assert isinstance(ok(c.get("/audit", params={"order_id": oid})), list)
    assert ok(c.get("/audit/verify"))["ok"] is True


def test_bad_request_rejected():
    assert c.post("/offers", json={"need": F["need"], "product_ids": [], "delivery_date": "2026-11-16"}).status_code == 422
