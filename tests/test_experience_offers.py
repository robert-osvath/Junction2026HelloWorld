"""Vimo cockpit: create-draft explains why a product has no offer (no network: fake Valio client)."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.catalog.aimo import AimoClient
from app.experience import router as experience
from app.main import app
from contracts import Need
from tests.conftest import fake_aimo_transport

D = date.today() + timedelta(days=3)  # create-draft rejects past dates: never hard-code one


@pytest.fixture
def fake_client(monkeypatch):
    client = AimoClient("test-key", transport=fake_aimo_transport(), sleep=lambda s: None)
    from app.orders import flow
    monkeypatch.setattr(experience, "get_client", lambda: client)
    monkeypatch.setattr(flow, "get_client", lambda: client)
    return client


def need(unit):
    return Need(text="t", product_intent="t", quantity=10, unit=unit, deliver_by=D)


def save_policy(client: TestClient, **overrides) -> str:
    body = {
        "buyer_name": "My kitchen", "buyer_type": "restaurant",
        "auto_approve_limit": 300, "budget": 900,
        "required_claims": [], "banned_allergens": [],
        "strict_allergens": False, "substitutes_allowed": True,
    }
    body.update(overrides)
    response = client.post("/experience/policies", json=body)
    assert response.status_code == 200, response.text
    return response.json()["policy_id"]


def test_unit_mismatch_says_what_to_do(fake_client):
    msg = experience.explain_no_offer("460000", need("L"), D)  # potato salad, priced per KG
    assert msg.startswith("Unit mismatch") and "kilograms (kg)" in msg and "Order it in kg" in msg


def test_other_reasons(fake_client):
    assert "not in the catalog" in experience.explain_no_offer("999999999", need("L"), D)
    assert "no usable price" in experience.explain_no_offer("460025", need("kg"), D)  # in catalog, no price fixture


def test_create_draft_returns_the_reason(fake_client):
    client = TestClient(app)
    r = TestClient(app).post("/experience/create-draft", json={
        "policy_id": save_policy(client, buyer_name="Hotel Kalevala", auto_approve_limit=2000, budget=2500),
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


def test_create_draft_binds_buyer_rules_to_the_mandate(fake_client):
    from app.orders import policies
    client = TestClient(app)
    policy_id = save_policy(client, banned_allergens=["AM", "AW"],
                            strict_allergens=True, substitutes_allowed=False)
    m = policies.get(policy_id)
    assert m is not None
    assert (m.buyer_name, m.auto_approve_limit_eur, m.budget_per_order_eur) == ("My kitchen", 300, 900)
    assert m.banned_allergens == ["AM", "AW"] and m.strict_allergens is True and m.substitutes_allowed is False


def test_agent_state_only_reports_cockpit_orders():
    from app.orders.store import get_order_store
    state = experience.backend_agent_state()
    assert state.order_id == "not-created" or any(
        o.id == state.order_id and experience.policies.is_cockpit_id(o.mandate_id) for o in get_order_store().list())


def test_create_draft_rejects_free_text_allergens(fake_client):
    r = TestClient(app).post("/experience/policies", json={
        "buyer_name": "My kitchen", "buyer_type": "restaurant", "auto_approve_limit": 300, "budget": 900,
        "required_claims": [], "banned_allergens": ["AM", "gluten"], "strict_allergens": False, "substitutes_allowed": True,
    })
    assert r.status_code == 422 and r.json()["detail"].startswith("Unknown allergen code(s): GLUTEN")


def test_create_draft_rejects_a_past_delivery_date_with_today(fake_client):
    client = TestClient(app)
    r = TestClient(app).post("/experience/create-draft", json={
        "policy_id": save_policy(client),
        "product_id": "463038", "product_intent": "milk", "quantity": 10, "unit": "L",
        "deliver_by": "2025-11-17",
    })
    assert r.status_code == 422 and "is in the past. Today is" in r.json()["detail"]


def test_draft_needs_no_saved_policy_and_no_product_id_and_duplicates_are_blocked(fake_client):
    client = TestClient(app)
    draft = {"product_intent": "milk", "quantity": 10, "unit": "L", "deliver_by": D.isoformat()}
    first = client.post("/experience/create-draft", json=draft)
    assert first.status_code == 200, first.text
    assert first.json()["mandate_id"] == "mandate-vimo-live"  # the default policy
    second = client.post("/experience/create-draft", json=draft)
    assert second.status_code == 409 and "matching order already exists" in second.json()["detail"]


def test_default_policy_without_save_and_edit_roundtrip():
    client = TestClient(app)
    d = client.get("/experience/policy", params={"buyer_name": "Brand New Bistro"}).json()
    assert d["buyer_name"] == "Brand New Bistro" and d["auto_approve_limit_eur"] == 500 and d["budget_per_order_eur"] == 2000
    assert client.get("/experience/policy").json()["buyer_name"] == "Hotel North"
    r = client.post("/experience/policies", json={"buyer_name": "Brand New Bistro", "auto_approve_limit": 150,
                                                  "banned_allergens": ["am"]})
    assert r.status_code == 200
    now = client.get("/experience/policy", params={"buyer_name": "Brand New Bistro"}).json()
    assert now["id"] == r.json()["policy_id"] and now["auto_approve_limit_eur"] == 150
    assert now["budget_per_order_eur"] == 2000 and now["banned_allergens"] == ["AM"]
