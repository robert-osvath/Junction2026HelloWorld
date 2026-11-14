"""T13 done-when: changing any past event makes verify fail."""
import os

import pytest

from app.mocks import load
from app.orders.audit import GENESIS, MariaDBStore, MemoryStore, event_hash, verify
from contracts import AuditVerify


def _fill(store, n=3):
    for i in range(n):
        store.append(order_id="order-1", actor="buyer-agent", action=f"step.{i}",
                     payload={"i": i, "eur": 1565.5}, human_summary=f"step {i}")
    return store.events()


def test_chain_links_and_verifies():
    store = MemoryStore()
    evs = _fill(store)
    assert evs[0].prev_hash == GENESIS
    assert all(b.prev_hash == a.hash for a, b in zip(evs, evs[1:]))
    assert store.verify().ok


def test_empty_chain_verifies():
    assert verify([], {}).ok


@pytest.mark.parametrize("field", ["actor", "action", "human_summary", "order_id", "ts", "prev_hash", "hash"])
def test_tampering_any_field_breaks_verify(field):
    store = MemoryStore()
    evs = _fill(store)
    e = evs[1]
    new = e.ts.replace(year=2020) if field == "ts" else "tampered"
    store._rows[1] = (e.model_copy(update={field: new}), store._rows[1][1])
    r = verify(store.events(), store.payloads())
    assert not r.ok and r.broken_at == e.id


def test_tampering_payload_breaks_verify():
    store = MemoryStore()
    evs = _fill(store)
    store._rows[0] = (evs[0], {"i": 0, "eur": 1.0})
    r = verify(store.events(), store.payloads())
    assert not r.ok and r.broken_at == evs[0].id


def test_filter_by_order():
    store = MemoryStore()
    _fill(store, 2)
    store.append(order_id="order-2", actor="human", action="order.approved", payload={}, human_summary="ok")
    assert [e.order_id for e in store.events("order-2")] == ["order-2"]


@pytest.mark.skipif(not os.environ.get("DB_URL_TEST"), reason="DB_URL_TEST not set (needs docker compose up)")
def test_mariadb_tamper_detected_and_history_kept():
    store = MariaDBStore(os.environ["DB_URL_TEST"])
    store.reset_for_tests()
    evs = _fill(store)
    assert store.verify().ok
    with store.conn() as c, c.cursor() as cur:
        cur.execute("UPDATE audit_events SET human_summary='tampered' WHERE id=%s", (evs[1].id,))
    r = store.verify()
    assert not r.ok and r.broken_at == evs[1].id
    with store.conn() as c, c.cursor() as cur:  # system versioning still holds the original row
        cur.execute("SELECT human_summary FROM audit_events FOR SYSTEM_TIME ALL WHERE id=%s ORDER BY row_end",
                    (evs[1].id,))
        assert [r[0] for r in cur.fetchall()] == ["step 1", "tampered"]


def test_unknown_actor_rejected():
    with pytest.raises(ValueError):
        MemoryStore().append(order_id="o", actor="hacker", action="x", payload={}, human_summary="x")


def test_payload_is_copied_on_append():
    store, pl = MemoryStore(), {"q": 1}
    store.append(order_id="o", actor="aimo", action="x", payload=pl, human_summary="x")
    pl["q"] = 2
    assert store.verify().ok


def test_fixture_hash_matches_hash_function():
    e = load("audit_event")
    assert e.hash == event_hash(e)


def test_audit_endpoint_accepts_both_param_spellings():
    from fastapi.testclient import TestClient
    from app.main import app
    import app.orders.audit as audit
    audit._store = MemoryStore()
    for oid in ("o-1", "o-2"):
        audit._store.append(order_id=oid, actor="aimo", action="x", payload={}, human_summary="x")
    c = TestClient(app)
    for p in ("order_id", "orderId"):
        assert [e["order_id"] for e in c.get("/audit", params={p: "o-2"}).json()] == ["o-2"]
    audit._store = None


@pytest.mark.skipif(not os.environ.get("DB_URL_TEST"), reason="DB_URL_TEST not set (needs docker compose up)")
def test_mariadb_concurrent_appends_and_tail_delete():
    from concurrent.futures import ThreadPoolExecutor
    store = MariaDBStore(os.environ["DB_URL_TEST"])
    store.reset_for_tests()
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda i: store.append(order_id="o", actor="aimo", action=f"a{i}", payload={"i": i},
                                           human_summary="x"), range(32)))
    assert store.verify() == AuditVerify(ok=True, checked=32)
    last = store.events()[-1]
    with store.conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM audit_events WHERE id=%s", (last.id,))
    r = store.verify()
    assert not r.ok and r.broken_at == last.id


@pytest.mark.skipif(not os.environ.get("DB_URL_TEST"), reason="DB_URL_TEST not set (needs docker compose up)")
def test_mariadb_rehashed_tail_edit_detected():
    from app.orders.audit import _sha
    store = MariaDBStore(os.environ["DB_URL_TEST"])
    store.reset_for_tests()
    last = _fill(store)[-1]
    forged = last.model_copy(update={"payload_hash": _sha({"i": 99})})
    forged = forged.model_copy(update={"hash": event_hash(forged)})
    with store.conn() as c, c.cursor() as cur:
        cur.execute("UPDATE audit_events SET payload=%s, payload_hash=%s, hash=%s WHERE id=%s",
                    ('{"i": 99}', forged.payload_hash, forged.hash, last.id))
    r = store.verify()
    assert not r.ok and r.broken_at == last.id


def test_db_url_parsing_ssl_and_encoded_password():
    from app.db import connect_args
    a = connect_args("mysql://app%40x:p%40ss%2Fw@db.example.com:4006/aimo?ssl=true")
    assert (a["user"], a["password"], a["host"], a["port"], a["database"]) == ("app@x", "p@ss/w", "db.example.com", 4006, "aimo")
    assert a["ssl"]["ca"]
    assert "ssl" not in connect_args("mysql://aimo:aimo@127.0.0.1:3306/aimo")
