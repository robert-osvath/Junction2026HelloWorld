"""T12 done-when: order state machine covered."""
import json
import os

import pytest
from fastapi.testclient import TestClient

import app.orders.audit as audit
import app.orders.store as store
from app.main import app
from app.mocks import FIXTURES, load
from app.orders.store import MariaDBStore, MemoryStore

c = TestClient(app)
NEED = json.loads((FIXTURES / "need.json").read_text())
OFFER = json.loads((FIXTURES / "offer.json").read_text())


@pytest.fixture(autouse=True)
def fresh_stores():
    audit._store, store._store = audit.MemoryStore(), MemoryStore()
    yield
    audit._store = store._store = None


def place(mandate="mandate-hotel-001", units=None):
    offer = OFFER if units is None else {**OFFER, "units_needed": units,
                                         "total_eur": round(units * OFFER["unit_price_eur"], 2)}
    return c.post("/orders", json={"mandate_id": mandate, "need": NEED, "lines": [offer]})


def actions(oid):
    return [(e["actor"], e["action"]) for e in c.get("/audit", params={"order_id": oid}).json()]


def test_happy_path_places_and_audits():
    r = place()
    assert r.status_code == 200
    o = r.json()
    assert o["status"] == "placed" and o["confirmation_no"].startswith("AIMO-SIM-") and len(o["verdicts"]) == 4
    assert o["id"].startswith("order-") and len(o["id"]) == 18
    assert actions(o["id"]) == [("buyer-agent", "order.requested"), ("juror:tier0", "verdict.approve"),
                                ("juror:spec", "verdict.approve"), ("juror:delivery", "verdict.approve"),
                                ("juror:cost", "verdict.approve"), ("aimo", "order.placed")]
    assert c.get("/audit/verify").json()["ok"] is True
    assert c.get(f"/orders/{o['id']}").json() == o


def test_over_auto_limit_then_approve():
    o = place("mandate-hotel-tight").json()
    assert o["status"] == "pending_approval" and o["confirmation_no"] is None
    a = c.post(f"/orders/{o['id']}/approve").json()
    assert a["status"] == "placed" and a["confirmation_no"].startswith("AIMO-SIM-")
    assert actions(o["id"])[-2:] == [("aimo", "order.pending_approval"), ("human", "order.approved")]
    assert c.get("/audit/verify").json()["ok"] is True


def test_over_auto_limit_then_reject():
    o = place("mandate-hotel-tight").json()
    r = c.post(f"/orders/{o['id']}/reject").json()
    assert r["status"] == "rejected" and r["confirmation_no"] is None
    assert actions(o["id"])[-1] == ("human", "order.rejected")


def test_over_budget_rejected():
    o = place(units=100).json()
    assert o["status"] == "rejected" and o["confirmation_no"] is None
    assert actions(o["id"])[-1] == ("aimo", "order.rejected")


def test_substitutes_proposed_then_reject(monkeypatch):
    import app.orders.service as svc
    monkeypatch.setattr(svc, "evaluate", lambda req: load("jury_result").model_copy(update={"decision": "substitutes"}))
    o = place().json()
    assert o["status"] == "substitutes_proposed" and len(o["substitutes"]) == 1
    assert c.post(f"/orders/{o['id']}/approve").status_code == 409
    assert c.post(f"/orders/{o['id']}/reject").json()["status"] == "rejected"


def test_invalid_transitions_409():
    placed = place().json()["id"]
    rejected = place(units=100).json()["id"]
    for oid, verb in [(placed, "approve"), (placed, "reject"), (rejected, "approve"), (rejected, "reject")]:
        r = c.post(f"/orders/{oid}/{verb}")
        assert r.status_code == 409 and "cannot" in r.text
    assert c.get(f"/orders/{placed}").json()["status"] == "placed"
    assert len(actions(placed)) == 6  # refused transitions leave no audit event


def test_404s():
    assert c.get("/orders/nope").status_code == 404
    assert c.post("/orders/nope/approve").status_code == 404
    assert c.post("/orders/nope/reject").status_code == 404
    assert place("mandate-nope").status_code == 404
    assert c.post("/orders", json={"mandate_id": "mandate-hotel-001", "need": NEED, "lines": []}).status_code == 422


def test_list_filter_newest_first():
    a = place().json()["id"]
    b = place("mandate-hotel-tight").json()["id"]
    d = place("mandate-hotel-tight").json()["id"]
    assert [o["id"] for o in c.get("/orders").json()] == [d, b, a]
    assert [o["id"] for o in c.get("/orders", params={"status": "pending_approval"}).json()] == [d, b]
    assert c.get("/orders", params={"status": "bogus"}).status_code == 422


@pytest.mark.skipif(not os.environ.get("DB_URL_TEST"), reason="DB_URL_TEST not set (needs docker compose up)")
def test_mariadb_store_roundtrip():
    s = MariaDBStore(os.environ["DB_URL_TEST"])
    s.reset_for_tests()
    o1, o2 = load("order"), load("order").model_copy(update={"id": "order-2", "status": "pending_approval"})
    s.save(o1)
    s.save(o2)
    assert s.get(o1.id) == o1 and s.get("nope") is None
    assert [o.id for o in s.list()] == ["order-2", o1.id]
    assert [o.id for o in s.list("pending_approval")] == ["order-2"]
    s.save(o2.model_copy(update={"status": "placed", "confirmation_no": "AIMO-SIM-000001"}))
    assert s.get("order-2").status == "placed" and len(s.list()) == 2


def test_faked_total_rejected_422():
    r = c.post("/orders", json={"mandate_id": "mandate-hotel-001", "need": NEED, "lines": [{**OFFER, "total_eur": 1.0}]})
    assert r.status_code == 422


def test_409_message_and_cost_verdict_consistent():
    o = place(mandate="mandate-hotel-tight").json()
    cost = next(v for v in o["verdicts"] if v["juror"] == "cost")
    assert o["status"] == "pending_approval" and cost["decision"] == "escalate"
    c.post(f"/orders/{o['id']}/approve")
    assert "cannot approve an order that is placed" in c.post(f"/orders/{o['id']}/approve").json()["detail"]
