"""Buyer policy registry used before any Vimo order can be drafted.

Policies are separate from orders: an order keeps the mandate that applied at
creation time, while the buyer can later save a revised policy for new work.
"""
import os
import threading
import uuid

from contracts import Mandate

from app.db import connect_args, pooled
from app.orders.mandates import MANDATES

_lock = threading.RLock()
POLICIES: dict[str, Mandate] = {}  # read-through cache; MariaDB remains the source when configured

SCHEMA = """
CREATE TABLE IF NOT EXISTS buyer_policies (
  id VARCHAR(64) PRIMARY KEY,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  body LONGTEXT NOT NULL,
  INDEX (created_at)
)
"""


class MemoryStore:
    def save(self, mandate: Mandate) -> None:
        POLICIES[mandate.id] = mandate

    def get(self, policy_id: str) -> Mandate | None:
        return POLICIES.get(policy_id)


class MariaDBStore:
    def __init__(self, url: str):
        self._args = connect_args(url)
        with self.conn() as conn, conn.cursor() as cur:
            cur.execute(SCHEMA)

    def conn(self):
        return pooled(self._args)

    def save(self, mandate: Mandate) -> None:
        with self.conn() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO buyer_policies (id, body) VALUES (%s, %s)",
                        (mandate.id, mandate.model_dump_json()))

    def get(self, policy_id: str) -> Mandate | None:
        with self.conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT body FROM buyer_policies WHERE id=%s", (policy_id,))
            row = cur.fetchone()
        return Mandate.model_validate_json(row[0]) if row else None


_store = None


def _store_for_runtime():
    global _store
    with _lock:
        if _store is None:
            _store = MariaDBStore(os.environ["DB_URL"]) if os.environ.get("DB_URL") else MemoryStore()
        return _store


def save(mandate: Mandate) -> Mandate:
    with _lock:
        policy_id = f"policy-{uuid.uuid4().hex[:12]}"
        saved = mandate.model_copy(update={"id": policy_id})
        _store_for_runtime().save(saved)
        POLICIES[policy_id] = saved
        # The order state machine resolves mandates from this registry.
        MANDATES[policy_id] = saved
        return saved


def get(policy_id: str) -> Mandate | None:
    cached = POLICIES.get(policy_id) or MANDATES.get(policy_id)
    if cached is not None:
        return cached
    saved = _store_for_runtime().get(policy_id)
    if saved is not None:
        POLICIES[policy_id] = saved
        MANDATES[policy_id] = saved
    return saved
