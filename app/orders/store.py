"""T12: order storage. MariaDB when DB_URL is set, else in-memory (same selection as the audit log)."""
import os
import threading
from datetime import UTC, datetime

import pymysql

from app.db import connect_args, pooled

from contracts import Order


class MemoryStore:
    def __init__(self):
        self._rows: dict[str, Order] = {}
        self._lock = threading.Lock()

    def save(self, order: Order) -> None:
        with self._lock:
            self._rows[order.id] = order.model_copy(deep=True)

    def get(self, order_id: str) -> Order | None:
        o = self._rows.get(order_id)
        return o.model_copy(deep=True) if o else None

    def list(self, status: str | None = None) -> list[Order]:
        return [o.model_copy(deep=True) for o in reversed(list(self._rows.values()))
                if status is None or o.status == status]


SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
  id VARCHAR(32) PRIMARY KEY,
  mandate_id VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  created_at DATETIME(6) NOT NULL,
  body LONGTEXT NOT NULL,
  INDEX (status), INDEX (created_at)
)
"""


class MariaDBStore:
    def __init__(self, url: str):
        self._args = connect_args(url)
        with self.conn() as c, c.cursor() as cur:
            cur.execute(SCHEMA)

    def conn(self):
        return pooled(self._args)

    def save(self, order: Order) -> None:
        with self.conn() as c, c.cursor() as cur:  # upsert; created_at is kept on update
            cur.execute("INSERT INTO orders (id, mandate_id, status, created_at, body) VALUES (%s,%s,%s,%s,%s) "
                        "ON DUPLICATE KEY UPDATE status=VALUES(status), body=VALUES(body)",
                        (order.id, order.mandate_id, order.status,
                         datetime.now(UTC).replace(tzinfo=None), order.model_dump_json()))

    def get(self, order_id: str) -> Order | None:
        with self.conn() as c, c.cursor() as cur:
            cur.execute("SELECT body FROM orders WHERE id=%s", (order_id,))
            r = cur.fetchone()
        return Order.model_validate_json(r[0]) if r else None

    def list(self, status: str | None = None) -> list[Order]:
        with self.conn() as c, c.cursor() as cur:
            cur.execute("SELECT body FROM orders" + (" WHERE status=%s" if status else "")
                        + " ORDER BY created_at DESC, id", (status,) if status else ())
            return [Order.model_validate_json(r[0]) for r in cur.fetchall()]

    def reset_for_tests(self):
        assert self._args["database"].endswith("_test"), "refusing to wipe a non-test database"
        with self.conn() as c, c.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS orders")
            cur.execute(SCHEMA)


_store = None
_store_lock = threading.Lock()


def get_order_store():
    global _store
    with _store_lock:
        if _store is None:
            _store = MariaDBStore(os.environ["DB_URL"]) if os.environ.get("DB_URL") else MemoryStore()
        return _store
