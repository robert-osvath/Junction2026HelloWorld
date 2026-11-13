"""T13: hash-chained audit log. MariaDB (system-versioned) when DB_URL is set, else in-memory."""
import hashlib
import json
import os
import threading
import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse

import pymysql

from contracts import AuditEvent, AuditVerify

GENESIS = "sha256:" + "0" * 64


def _sha(obj) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def _ts(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat(timespec="microseconds")


def event_hash(e: AuditEvent) -> str:
    return _sha({"id": e.id, "ts": _ts(e.ts), "order_id": e.order_id, "actor": e.actor, "action": e.action,
                 "payload_hash": e.payload_hash, "prev_hash": e.prev_hash, "human_summary": e.human_summary})


def make_event(prev_hash: str, *, order_id, actor, action, payload, human_summary) -> AuditEvent:
    e = AuditEvent(id=f"evt-{uuid.uuid4().hex[:12]}", ts=datetime.now(UTC), order_id=order_id, actor=actor,
                   action=action, payload_hash=_sha(payload), prev_hash=prev_hash, hash="",
                   human_summary=human_summary)
    return e.model_copy(update={"hash": event_hash(e)})


def verify(events: list[AuditEvent], payloads: dict[str, object]) -> AuditVerify:
    """events in chain order. shortcut: deleting the newest events is not detected here; system versioning shows it."""
    prev = GENESIS
    for i, e in enumerate(events):
        if e.prev_hash != prev or e.hash != event_hash(e) or e.payload_hash != _sha(payloads.get(e.id)):
            return AuditVerify(ok=False, checked=i, broken_at=e.id)
        prev = e.hash
    return AuditVerify(ok=True, checked=len(events))


class MemoryStore:
    def __init__(self):
        self._rows: list[tuple[AuditEvent, object]] = []
        self._lock = threading.Lock()

    def append(self, **kw) -> AuditEvent:
        with self._lock:
            e = make_event(self._rows[-1][0].hash if self._rows else GENESIS, **kw)
            self._rows.append((e, kw["payload"]))
            return e

    def events(self, order_id: str | None = None) -> list[AuditEvent]:
        return [e for e, _ in self._rows if order_id is None or e.order_id == order_id]

    def payloads(self) -> dict[str, object]:
        return {e.id: p for e, p in self._rows}

    def verify(self) -> AuditVerify:
        return verify(self.events(), self.payloads())


SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
  seq BIGINT AUTO_INCREMENT PRIMARY KEY,
  id VARCHAR(32) NOT NULL UNIQUE,
  ts DATETIME(6) NOT NULL,
  order_id VARCHAR(64) NULL,
  actor VARCHAR(64) NOT NULL,
  action VARCHAR(64) NOT NULL,
  payload LONGTEXT NOT NULL,
  payload_hash CHAR(71) NOT NULL,
  prev_hash CHAR(71) NOT NULL UNIQUE,  -- a second writer on the same tip fails instead of forking the chain
  hash CHAR(71) NOT NULL UNIQUE,
  human_summary TEXT NOT NULL,
  INDEX (order_id)
) WITH SYSTEM VERSIONING
"""
COLS = "id, ts, order_id, actor, action, payload, payload_hash, prev_hash, hash, human_summary"


class MariaDBStore:
    def __init__(self, url: str):
        u = urlparse(url)
        self._args = dict(host=u.hostname, port=u.port or 3306, user=u.username, password=u.password or "",
                          database=u.path.lstrip("/"), autocommit=True, charset="utf8mb4")
        with self.conn() as c, c.cursor() as cur:
            cur.execute(SCHEMA)

    def conn(self):
        return pymysql.connect(**self._args)

    def append(self, **kw) -> AuditEvent:
        with self.conn() as c, c.cursor() as cur:
            c.begin()
            cur.execute("SELECT hash FROM audit_events ORDER BY seq DESC LIMIT 1 FOR UPDATE")
            row = cur.fetchone()
            e = make_event(row[0] if row else GENESIS, **kw)
            cur.execute(f"INSERT INTO audit_events ({COLS}) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (e.id, e.ts.astimezone(UTC).replace(tzinfo=None), e.order_id, e.actor, e.action,
                         json.dumps(kw["payload"], default=str), e.payload_hash, e.prev_hash, e.hash,
                         e.human_summary))
            c.commit()
            return e

    def _rows(self, order_id: str | None = None):
        sql = f"SELECT {COLS} FROM audit_events" + (" WHERE order_id=%s" if order_id else "") + " ORDER BY seq"
        with self.conn() as c, c.cursor() as cur:
            cur.execute(sql, (order_id,) if order_id else ())
            for r in cur.fetchall():
                yield AuditEvent(id=r[0], ts=r[1].replace(tzinfo=UTC), order_id=r[2], actor=r[3], action=r[4],
                                 payload_hash=r[6], prev_hash=r[7], hash=r[8], human_summary=r[9]), json.loads(r[5])

    def events(self, order_id: str | None = None) -> list[AuditEvent]:
        return [e for e, _ in self._rows(order_id)]

    def verify(self) -> AuditVerify:
        rows = list(self._rows())
        return verify([e for e, _ in rows], {e.id: p for e, p in rows})

    def reset_for_tests(self):
        assert self._args["database"].endswith("_test"), "refusing to wipe a non-test database"
        with self.conn() as c, c.cursor() as cur:
            cur.execute("DELETE FROM audit_events")


_store = None


def get_store():
    global _store
    if _store is None:
        _store = MariaDBStore(os.environ["DB_URL"]) if os.environ.get("DB_URL") else MemoryStore()
    return _store
