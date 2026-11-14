"""Vimo cockpit: create-draft explains why a product has no offer (no network: fake Valio client)."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.catalog.aimo import AimoClient
from app.experience import router as experience
from app.main import app
from contracts import Need
from tests.conftest import fake_aimo_transport

D = date(2026, 11, 16)


@pytest.fixture
def fake_client(monkeypatch):
    client = AimoClient("test-key", transport=fake_aimo_transport(), sleep=lambda s: None)
    monkeypatch.setattr(experience, "get_client", lambda: client)
    return client


def need(unit):
    return Need(text="t", product_intent="t", quantity=10, unit=unit, deliver_by=D)


def test_unit_mismatch_says_what_to_do(fake_client):
    msg = experience.explain_no_offer("460000", need("L"), D)  # potato salad, priced per KG
    assert msg.startswith("Unit mismatch") and "kilograms (kg)" in msg and "Order it in kg" in msg


def test_other_reasons(fake_client):
    assert "not in the catalog" in experience.explain_no_offer("999999999", need("L"), D)
    assert "no usable price" in experience.explain_no_offer("460025", need("kg"), D)  # in catalog, no price fixture


def test_create_draft_returns_the_reason(fake_client):
    r = TestClient(app).post("/experience/create-draft", json={
        "buyer_name": "Hotel Kalevala", "auto_approve_limit": 2000, "budget": 2500,
        "product_id": "460000", "product_intent": "potato salad", "quantity": 10, "unit": "L",
        "deliver_by": D.isoformat(),
    })
    assert r.status_code == 422 and r.json()["detail"].startswith("Unit mismatch")


def test_remaining_reasons_mirror_t4(monkeypatch, tmp_path):
    import os, shutil
    copy = tmp_path / "catalog.db"  # this test deletes rows: never touch the session DB
    shutil.copy(os.environ["CATALOG_DB"], copy)
    monkeypatch.setenv("CATALOG_DB", str(copy))
    from app.catalog.aimo import AvailabilityInfo, PriceInfo

    class Stub:
        def __init__(self, price, avail):
            self.price, self.avail = price, avail

        def prices(self, ids, d):
            return {ids[0]: self.price} if self.price else {}

        def availability(self, ids, d):
            return {ids[0]: self.avail} if self.avail else {}

    ok_price = PriceInfo(product_id="463038", catalog_price_eur=31.31, comparison_price_eur=1.57, comparison_unit="L")
    scheduled_no_date = AvailabilityInfo(product_id="463038", logistic_model="Terminal")

    def explain(price, avail, unit="L", pid="463038"):
        monkeypatch.setattr(experience, "get_client", lambda: Stub(price, avail))
        return experience.explain_no_offer(pid, need(unit), D)

    assert "no usable price" in explain(ok_price.model_copy(update={"catalog_price_eur": -1.0}), None)
    assert "no delivery schedule" in explain(ok_price, None)
    assert "no delivery schedule" in explain(ok_price, scheduled_no_date)  # logisticModel set, no date
    assert "no per-litre or per-kilogram" in explain(ok_price.model_copy(update={"comparison_unit": "KPL"}), None)
    # Eila is sold per KI (2 x ST); a pcs request works only when the sales-unit size is known
    with experience.catalog_store.connect() as conn:
        conn.execute("DELETE FROM product_units WHERE product_id = '463038'")
    assert "no known number of pieces" in explain(ok_price, None, unit="pcs")
