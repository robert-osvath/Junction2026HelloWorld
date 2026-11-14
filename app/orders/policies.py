"""Buyer policy registry. Every buyer has a ready default policy (no setup needed); a saved policy
overrides it and the buyer can edit it any time.

Policies are separate from orders: an order keeps the mandate that applied at
creation time, while the buyer can later save a revised policy for new work.
"""
import os
import threading
import uuid

from contracts import Mandate

from app.db import connect_args, pooled
from app.orders.mandates import MANDATES

DEFAULT_BUYER = "Hotel North"
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


def _same(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def is_cockpit_id(mandate_id: str) -> bool:
    """Mandate ids that belong to Vimo buyers (saved or default policies)."""
    return mandate_id.startswith(("policy-", "default-")) or mandate_id == "mandate-vimo-live"


class MemoryStore:
    """Process-lifetime store (no DB_URL). Kept apart from the POLICIES/MANDATES caches so clearing the
    caches behaves like a restart against a real store; the whole store itself is lost on a real restart,
    exactly like the in-memory order store."""

    def __init__(self) -> None:
        self._rows: dict[str, Mandate] = {}

    def save(self, mandate: Mandate) -> None:
        self._rows[mandate.id] = mandate

    def save_if_absent(self, mandate: Mandate) -> None:
        self._rows.setdefault(mandate.id, mandate)

    def get(self, policy_id: str) -> Mandate | None:
        return self._rows.get(policy_id)

    def latest(self, buyer_name: str) -> Mandate | None:
        return next((m for m in reversed(list(self._rows.values())) if _same(m.buyer_name, buyer_name)), None)


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

    def save_if_absent(self, mandate: Mandate) -> None:
        with self.conn() as conn, conn.cursor() as cur:  # several workers may register the same default
            cur.execute("INSERT IGNORE INTO buyer_policies (id, body) VALUES (%s, %s)",
                        (mandate.id, mandate.model_dump_json()))

    def get(self, policy_id: str) -> Mandate | None:
        with self.conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT body FROM buyer_policies WHERE id=%s", (policy_id,))
            row = cur.fetchone()
        return Mandate.model_validate_json(row[0]) if row else None

    def latest(self, buyer_name: str) -> Mandate | None:
        with self.conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT body FROM buyer_policies ORDER BY created_at DESC, id LIMIT 200")
            rows = cur.fetchall()
        return next((m for m in (Mandate.model_validate_json(r[0]) for r in rows)
                     if _same(m.buyer_name, buyer_name)), None)


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


def resolve(mandate_id: str | None) -> Mandate | None:
    """The one mandate lookup: cache (saved/fixture/default) -> store. Default policies are persisted
    when first used (see default()), so orders under them stay actionable after a restart, and an
    arbitrary `default-...` id that was never issued does not resolve."""
    if not mandate_id:
        return None
    cached = POLICIES.get(mandate_id) or MANDATES.get(mandate_id)
    if cached is not None:
        return cached
    with _lock:
        saved = _store_for_runtime().get(mandate_id)
    if saved is not None:
        POLICIES[saved.id] = saved
        MANDATES[saved.id] = saved
        return saved
    return None


get = resolve


def default(buyer_name: str | None = None) -> Mandate:
    """Simple ready-made policy: auto-approve up to EUR 500, budget EUR 2,000, no claim/allergen rules,
    substitutes allowed. Registered so orders made under it stay actionable."""
    name = (buyer_name or "").strip() or DEFAULT_BUYER
    base = MANDATES["mandate-vimo-live"]
    if _same(name, DEFAULT_BUYER):
        return base
    slug = "-".join("".join(c if c.isalnum() else " " for c in name.casefold()).split())[:48] or "buyer"
    m = base.model_copy(update={"id": f"default-{slug}", "buyer_name": name})
    if m.id not in MANDATES:
        with _lock:
            _store_for_runtime().save_if_absent(m)  # persist on first use: restart-safe, never invented
        MANDATES.setdefault(m.id, m)
    return MANDATES[m.id]


def current(buyer_name: str | None = None) -> Mandate:
    """The buyer's current policy: latest saved one, else the default."""
    name = (buyer_name or "").strip() or DEFAULT_BUYER
    with _lock:
        saved = _store_for_runtime().latest(name)
    if saved is not None:
        POLICIES[saved.id] = saved
        MANDATES[saved.id] = saved
        return saved
    return default(name)
