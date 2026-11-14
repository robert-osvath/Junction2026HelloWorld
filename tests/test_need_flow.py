"""Need-based ordering (no network): ranking, outcomes, endpoint errors, audit. The jury, offers and
substitute ranker are stubbed at the flow module so every branch is deterministic."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import app.orders.audit as audit
import app.orders.store as store
from app.catalog.aimo import AimoError
from app.main import app
from app.orders import flow
from app.orders.store import MemoryStore
from contracts import JuryResult, Mandate, Need, Offer, Verdict

D = date.today() + timedelta(days=3)
NEED = Need(text="1000 L milk", product_intent="milk", quantity=1000, unit="L", deliver_by=D)
MANDATE = Mandate(id="mandate-hotel-001", buyer_name="Hotel", buyer_type="hotel", budget_per_order_eur=2500,
                  auto_approve_limit_eur=2000)


def offer(pid, total, allergens="declared"):
    return Offer(product_id=pid, name=f"Milk {pid}", sales_unit="KI", units_needed=50, unit_price_eur=total / 50,
                 comparison_price_eur=1.5, comparison_unit="L", total_eur=total, available_date=D,
                 closing_time=None, sales_block=False, logistic_model="Stock",
                 evidence={"stock": "verified", "allergens": allergens})


def jury(decision, conf=0.9):
    v = {"auto_order": "approve", "human_approval": "escalate"}.get(decision, "reject")
    return JuryResult(verdicts=[Verdict(juror="spec", decision=v, confidence=conf, reasons=[f"{decision} reason"])],
                      decision=decision, chosen_product_id=None)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    audit._store, store._store = audit.MemoryStore(), MemoryStore()
    monkeypatch.setattr(flow, "get_client", lambda: None)
    monkeypatch.setattr(flow, "search", lambda *a, **k: [type("I", (), {"product_id": "x"})()])
    yield
    audit._store = store._store = None


def setup(monkeypatch, decisions, subs=()):
    """decisions: product_id -> JuryResult. Offers are built from the ids' totals."""
    offers = [offer(pid, total) for pid, (total, _) in decisions.items()]
    monkeypatch.setattr(flow, "get_offers", lambda conn, client, req: offers)
    monkeypatch.setattr(flow, "evaluate", lambda req: decisions[req.offers[0].product_id][1])
    monkeypatch.setattr(flow, "substitutes", lambda req: list(subs))
    monkeypatch.setattr(flow, "resolve_catalog_items", lambda ids: {})


def actions(oid):
    return [e.action for e in audit.get_store().events(oid)]


def test_cheapest_auto_order_beats_pricier_one(monkeypatch):
    setup(monkeypatch, {"dear": (1500, jury("auto_order")), "cheap": (1200, jury("auto_order")),
                        "hold": (900, jury("human_approval"))})
    o = flow.order_from_need(MANDATE, NEED)
    assert o.status == "placed" and [l.product_id for l in o.lines] == ["cheap"] and o.confirmation_no
    assert "jury.compared" in actions(o.id) and actions(o.id)[-1] == "order.placed"
    compared = next(e for e in audit.get_store().events(o.id) if e.action == "jury.compared")
    assert "Winner Milk cheap" in compared.human_summary and "Milk dear" in compared.human_summary


def test_confidence_breaks_a_price_tie(monkeypatch):
    setup(monkeypatch, {"a": (1000, jury("auto_order", 0.6)), "b": (1000, jury("auto_order", 0.95))})
    assert flow.order_from_need(MANDATE, NEED).lines[0].product_id == "b"


def test_human_approval_only_when_nothing_auto_orders(monkeypatch):
    setup(monkeypatch, {"x": (1800, jury("human_approval")), "y": (1700, jury("human_approval")),
                        "z": (500, jury("reject"))})
    o = flow.order_from_need(MANDATE, NEED)
    assert o.status == "pending_approval" and o.lines[0].product_id == "y" and o.confirmation_no is None
    held = audit.get_store().events(o.id)[-1]
    assert "human approval" in held.human_summary and "spec: human_approval reason" in held.human_summary


def test_over_threshold_goes_to_pending_approval_with_real_jury(monkeypatch):
    # real (offline) jury + fake Valio: Eila 1,000 L is EUR 1,565.50 > auto-approve limit 1,000
    from app.catalog.aimo import AimoClient
    from tests.conftest import fake_aimo_transport
    client = AimoClient("k", transport=fake_aimo_transport(), sleep=lambda s: None)
    monkeypatch.setattr(flow, "get_client", lambda: client)
    tight = MANDATE.model_copy(update={"id": "mandate-hotel-tight", "auto_approve_limit_eur": 1000})
    o = flow.order_from_need(tight, NEED, product_hint="463038")
    assert o.status == "pending_approval" and o.lines[0].product_id == "463038"


def test_all_blocked_proposes_substitutes_without_banned_allergens(monkeypatch):
    setup(monkeypatch, {"p": (1200, jury("substitutes")), "q": (1100, jury("reject"))},
          subs=[offer("clean", 1300), offer("nutty", 1250)])
    from types import SimpleNamespace as NS
    monkeypatch.setattr(flow, "resolve_catalog_items", lambda ids: {
        "nutty": NS(allergens_contains=["AN"], allergens_may_contain=[]),
        "clean": NS(allergens_contains=[], allergens_may_contain=[])})
    m = MANDATE.model_copy(update={"banned_allergens": ["AN"]})
    o = flow.order_from_need(m, NEED)
    assert o.status == "substitutes_proposed" and o.lines[0].product_id == "p"  # the substitutes-verdict offer
    assert [s.product_id for s in o.substitutes] == ["clean"]


def test_substitutes_not_allowed_rejects_with_reasons(monkeypatch):
    setup(monkeypatch, {"p": (1200, jury("reject"))})
    o = flow.order_from_need(MANDATE.model_copy(update={"substitutes_allowed": False}), NEED)
    assert o.status == "rejected" and o.substitutes == []
    assert "reject reason" in audit.get_store().events(o.id)[-1].human_summary


def test_strict_allergens_missing_data_is_not_acceptable(monkeypatch):
    offers = [offer("nodata", 100, allergens="missing"), offer("ok", 900)]
    monkeypatch.setattr(flow, "get_offers", lambda *a: offers)
    monkeypatch.setattr(flow, "evaluate", lambda req: jury("auto_order"))
    monkeypatch.setattr(flow, "substitutes", lambda req: [])
    o = flow.order_from_need(MANDATE.model_copy(update={"strict_allergens": True}), NEED)
    assert o.status == "placed" and o.lines[0].product_id == "ok"


def test_simulate_out_of_stock_blocks_every_candidate(monkeypatch):
    seen = []
    monkeypatch.setattr(flow, "get_offers", lambda *a: [offer("a", 1000)])
    monkeypatch.setattr(flow, "evaluate", lambda req: seen.append(req.offers[0]) or jury("substitutes"))
    monkeypatch.setattr(flow, "substitutes", lambda req: [offer("alt", 1100)])
    monkeypatch.setattr(flow, "resolve_catalog_items", lambda ids: {})
    o = flow.order_from_need(MANDATE, NEED, simulate_out_of_stock=True)
    assert seen[0].sales_block and seen[0].evidence.stock == "none" and o.status == "substitutes_proposed"


def test_nothing_found_is_rejected_with_a_reason(monkeypatch):
    monkeypatch.setattr(flow, "search", lambda *a, **k: [])
    o = flow.order_from_need(MANDATE, NEED.model_copy(update={"product_intent": "unobtainium"}))
    assert o.status == "rejected" and o.lines == []
    assert "No product matches unobtainium" in audit.get_store().events(o.id)[-1].human_summary


def test_endpoint_unknown_mandate_404_and_aimo_error_502(monkeypatch):
    c = TestClient(app)
    body = {"mandate_id": "nope", "need": NEED.model_dump(mode="json")}
    assert c.post("/orders/from-need", json=body).status_code == 404

    def boom(*a, **k):
        raise AimoError("down")
    monkeypatch.setattr(flow, "get_offers", boom)
    r = c.post("/orders/from-need", json={**body, "mandate_id": "mandate-hotel-001"})
    assert r.status_code == 502


def test_endpoint_places_an_order(monkeypatch):
    setup(monkeypatch, {"a": (1000, jury("auto_order"))})
    r = TestClient(app).post("/orders/from-need", json={"mandate_id": "mandate-hotel-001",
                                                       "need": NEED.model_dump(mode="json")})
    assert r.status_code == 200 and r.json()["status"] == "placed"


# --- review fixes ---

def _forget_non_fixture_mandates():
    """Simulate a restart: only fixture mandates survive."""
    from app.orders import mandates, policies
    keep = {"mandate-hotel-001", "mandate-hotel-tight", "mandate-hospital-001", "mandate-vimo-live"}
    for reg in (policies.POLICIES, mandates.MANDATES):
        for k in [k for k in reg if k not in keep]:
            del reg[k]


def test_default_policy_has_no_claim_or_allergen_rules():
    from app.orders import policies
    for m in (policies.default(), policies.default("Cafe Uusi"), policies.current("Another One")):
        assert m.required_claims == [] and m.banned_allergens == [] and not m.strict_allergens
    d = TestClient(app).get("/experience/policy").json()
    assert d["required_claims"] == [] and d["banned_allergens"] == []
    from app.orders.mandates import MANDATES
    assert MANDATES["mandate-hotel-001"].required_claims == ["FREE_FROM_LACTOSE"]  # demo fixture untouched


def test_default_orders_stay_actionable_after_restart(monkeypatch):
    from app.orders import policies
    c = TestClient(app)
    m = policies.default("Cafe Uusi")
    setup(monkeypatch, {"p": (1200, jury("human_approval"))})
    a = flow.order_from_need(m, NEED)
    b = flow.order_from_need(m, NEED)
    setup(monkeypatch, {"p": (1200, jury("substitutes"))}, subs=[offer("alt", 1300)])
    sub = flow.order_from_need(m, NEED)
    assert (a.status, sub.status) == ("pending_approval", "substitutes_proposed")
    _forget_non_fixture_mandates()
    assert c.post(f"/orders/{a.id}/approve").json()["status"] == "placed"
    assert c.post(f"/orders/{b.id}/reject", json={"reason": "no"}).json()["status"] == "rejected"
    setup(monkeypatch, {"alt": (1300, jury("auto_order"))})
    monkeypatch.setattr("app.orders.service.evaluate", lambda req: jury("auto_order"))
    r = c.post(f"/orders/{sub.id}/accept-substitute", json={"product_id": "alt"})
    assert r.status_code == 200 and r.json()["status"] == "placed"
    _forget_non_fixture_mandates()
    r = c.post("/orders/from-need", json={"mandate_id": m.id, "need": NEED.model_dump(mode="json")})
    assert r.status_code == 200


def test_duplicate_guard_survives_restart(monkeypatch):
    from app.experience import router as experience
    from app.orders import policies
    setup(monkeypatch, {"p": (1200, jury("auto_order"))})
    monkeypatch.setattr(experience.order_flow, "order_from_need", flow.order_from_need)
    c = TestClient(app)
    body = {"buyer_name": "Cafe Uusi", "product_intent": "milk", "quantity": 10, "unit": "L",
            "deliver_by": D.isoformat()}
    assert c.post("/experience/create-draft", json=body).status_code == 200
    _forget_non_fixture_mandates()
    r = c.post("/experience/create-draft", json=body)
    assert r.status_code == 409


def test_from_need_client_errors_are_422(monkeypatch):
    from app.catalog.offers import InvalidOfferRequest
    c = TestClient(app)
    body = {"mandate_id": "mandate-hotel-001", "need": {**NEED.model_dump(mode="json"), "quantity": 0}}
    assert c.post("/orders/from-need", json=body).status_code == 422

    def bad(*a, **k):
        raise InvalidOfferRequest("nope")
    monkeypatch.setattr(flow, "get_offers", bad)
    r = c.post("/orders/from-need", json={**body, "need": NEED.model_dump(mode="json")})
    assert r.status_code == 422 and r.json()["detail"] == "nope"


def test_create_draft_with_hint_without_offers_persists_nothing(monkeypatch):
    from app.experience import router as experience
    monkeypatch.setattr(flow, "get_offers", lambda *a: [])
    monkeypatch.setattr(experience, "explain_no_offer", lambda *a: "because")
    before = len(store.get_order_store().list())
    r = TestClient(app).post("/experience/create-draft", json={
        "product_id": "463038", "product_intent": "milk", "quantity": 10, "unit": "L", "deliver_by": D.isoformat()})
    assert r.status_code == 422 and r.json()["detail"] == "because"
    assert len(store.get_order_store().list()) == before and audit.get_store().events(None) == []
