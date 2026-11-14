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
