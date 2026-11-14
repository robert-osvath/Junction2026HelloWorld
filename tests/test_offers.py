from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app.catalog.aimo import AimoClient, AimoError, AvailabilityInfo, PriceInfo, get_client
from app.catalog.offers import build_offers, roundest_content, round_to_lot
from app.main import app
from contracts import Need

D = date(2026, 11, 16)


def need(q=1000, unit="L"):
    return Need(text="t", product_intent="milk", quantity=q, unit=unit, deliver_by=D)


def row(pid="463038", unit="KI", base="ST", lot=0.01, allergen=1, rel=None, size=2.0, en="Eila", fi="Eila fi"):
    return {"id": pid, "name_en": en, "name_fi": fi, "sales_unit": unit, "base_unit": base,
            "allowed_lot_size": lot, "has_allergen_data": allergen, "delivery_reliability": rel,
            "size_in_base_units": size}


def price(pid="463038", cat=31.31, comp=1.57, unit="L"):
    return PriceInfo(product_id=pid, catalog_price_eur=cat, comparison_price_eur=comp, comparison_unit=unit)


def avail(pid="463038", model="Terminal", qty=None):
    return AvailabilityInfo(product_id=pid, available_date=D, closing_time="2026-11-12T15:00:00+0200",
                            logistic_model=model, quantity=qty)


def one(n=None, r=None, p=None, a=None, pid="463038"):
    return build_offers(n or need(), [pid], {pid: r or row(pid)}, {pid: p or price(pid)},
                        {pid: a or avail(pid)})


def test_eila_1000_l():
    (o,) = one()
    assert (o.units_needed, o.unit_price_eur, o.comparison_price_eur, o.comparison_unit) == (50, 31.31, 1.57, "L")
    assert o.total_eur == 1565.50
    assert o.logistic_model == "Terminal" and o.sales_block is False and o.available_date == D
    assert o.closing_time.tzinfo is not None
    assert (o.evidence.stock, o.evidence.allergens, o.evidence.reliability) == ("estimated", "declared", None)
    assert o.name == "Eila"


def test_name_fallback_and_missing_allergens():
    (o,) = one(r=row(en=None, allergen=0, rel=0.9))
    assert o.name == "Eila fi"
    assert (o.evidence.allergens, o.evidence.reliability) == ("missing", 0.9)


@pytest.mark.parametrize("cat,comp,expected", [
    (31.31, 1.57, 20), (1.96, 4.9, 0.4), (20.0, 2.0, 10), (24.5, 4.9, 5), (12.5, 2.5, 5),
])
def test_roundest_content(cat, comp, expected):
    assert roundest_content(cat, comp) == pytest.approx(expected)


def test_unit_mismatch_skipped():
    assert one(r=row("460000", "RAS", "RAS"), p=price("460000", 1.96, 4.9, "KG"), a=avail("460000"),
               pid="460000") == []
    assert one(n=need(10, "kg"), r=row("460000", "RAS", "RAS"), p=price("460000", 1.96, 4.9, "KG"),
               a=avail("460000"), pid="460000")[0].units_needed == 25  # 10 kg / 0.4 kg


def test_pcs():
    (o,) = one(n=need(101, "pcs"))  # KI = 2 base units
    assert o.units_needed == 51
    (o,) = one(n=need(7, "pcs"), r=row("460000", "RAS", "RAS", size=1.0), p=price("460000", 1.96, 4.9, "KG"),
               a=avail("460000"), pid="460000")
    assert o.units_needed == 7 and o.comparison_unit == "KG"


def test_lot_size_rounding():
    (o,) = one(r=row(lot=12))
    assert o.units_needed == 60 and o.total_eur == round(60 * 31.31, 2)


def test_stock_verified_vs_none():
    assert one(a=avail(model="Stock", qty=50))[0].evidence.stock == "verified"
    assert one(a=avail(model="Stock", qty=49))[0].evidence.stock == "none"
    assert one(a=avail(model="Stock", qty=None))[0].evidence.stock == "none"


def test_skips_and_order():
    rows = {"a": row("a"), "b": row("b"), "c": row("c"), "d": row("d")}
    prices = {"a": price("a"), "c": price("c"), "d": price("d")}
    av = {"a": avail("a"), "b": avail("b"), "d": avail("d")}
    out = build_offers(need(), ["d", "a", "b", "c", "zzz"], rows, prices, av)
    assert [o.product_id for o in out] == ["d", "a"]  # b no price, c no availability, zzz not in catalog


def test_missing_comparison_price_skipped():
    assert one(p=PriceInfo(product_id="463038", catalog_price_eur=3.0)) == []


# --- endpoint ---
c = TestClient(app)
BODY = {"need": need().model_dump(mode="json"), "product_ids": ["463038"], "delivery_date": "2026-11-16"}


def test_endpoint_eila():
    r = c.post("/offers", json=BODY)
    assert r.status_code == 200
    (o,) = r.json()
    assert o["product_id"] == "463038" and o["units_needed"] == 50 and o["total_eur"] == 1565.5


def test_endpoint_skips_unknown_ids():
    r = c.post("/offers", json={**BODY, "product_ids": ["999999999", "463038"]})
    assert [o["product_id"] for o in r.json()] == ["463038"]


def test_endpoint_aimo_error_502():
    bad = AimoClient("k", transport=httpx.MockTransport(lambda req: httpx.Response(400, text="no")),
                     sleep=lambda s: None)
    prev = app.dependency_overrides[get_client]
    app.dependency_overrides[get_client] = lambda: bad
    try:
        r = c.post("/offers", json=BODY)
    finally:
        app.dependency_overrides[get_client] = prev
    assert r.status_code == 502


@pytest.mark.parametrize("n,lot,expected", [
    (5, 0.01, 5), (5, 1, 5), (4, 1.5, 6), (6, 1.5, 6), (7, 1.5, 9), (3, 2.5, 5), (6, 2.5, 10),
    (7, 6, 12), (1, 6, 6),
])
def test_round_to_lot(n, lot, expected):
    assert round_to_lot(n, lot) == expected


def test_roundest_content_rejects_non_positive():
    for a, b in [(0, 1.0), (1.0, 0), (-1.0, 2.0), (1.0, -2.0)]:
        with pytest.raises(ValueError):
            roundest_content(a, b)


def test_non_positive_prices_skipped():
    assert one(p=price(cat=0.0)) == []
    assert one(p=price(comp=0.0)) == []
    assert one(p=price(cat=-1.0)) == []


@pytest.mark.parametrize("q", ["Infinity", "NaN", "0", "-5"])
def test_endpoint_bad_quantity_422(q):
    raw = ('{"need": {"text": "t", "product_intent": "m", "quantity": %s, "unit": "L", '
           '"deliver_by": "2026-11-16"}, "product_ids": ["463038"], "delivery_date": "2026-11-16"}' % q)
    r = c.post("/offers", content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 422
