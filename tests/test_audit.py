"""T13 done-when: changing any past event makes verify fail."""
import os

import pytest

from app.orders.audit import GENESIS, MariaDBStore, MemoryStore, verify


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
