"""T12: order state machine. Router stays thin; every state change writes an audit event (T13)."""
import random
import threading
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException

from app.jury.router import evaluate, substitutes
from app.orders.audit import get_store
from app.orders import policies
from app.orders.store import get_order_store
from contracts import JuryRequest, Mandate, Need, Offer, Order, OrderRequest, SubstitutesRequest, Verdict

STATUS = {"auto_order": "placed", "human_approval": "pending_approval",
          "substitutes": "substitutes_proposed", "reject": "rejected"}
_accepting: set[str] = set()  # orders with a substitute acceptance in flight (no double new order)
_lock = threading.Lock()  # shortcut: process-local; serialises approve/reject races, use SELECT..FOR UPDATE if multi-worker


def _audit(order_id, actor, action, payload, summary):
    get_store().append(order_id=order_id, actor=actor, action=action, payload=payload, human_summary=summary)


def _what(o: Order) -> str:
    if not o.lines:
        return f"order for {o.need.text}"
    l = o.lines[0]
    more = f" (+{len(o.lines) - 1} more lines)" if len(o.lines) > 1 else ""
    return f"{l.units_needed} x {l.name}{more} for {sum(x.total_eur for x in o.lines):,.2f} EUR"


def _confirm() -> str:
    return f"AIMO-SIM-{random.randrange(10**6):06d}"  # placement is simulated: no order API exists


def create(req: OrderRequest) -> Order:
    mandate = policies.resolve(req.mandate_id)
    if mandate is None:
        raise HTTPException(404, f"unknown mandate {req.mandate_id!r}")
    if not req.lines:
        raise HTTPException(422, "order needs at least one line")
    # lines come from an external agent: never trust its totals. shortcut: re-check arithmetic only;
    # re-pricing lines server-side from product_ids via P1's offer builder (T4) is the real fix
    bad = [l.product_id for l in req.lines if abs(l.units_needed * l.unit_price_eur - l.total_eur) > 0.01]
    if bad:
        raise HTTPException(422, f"total_eur != units_needed * unit_price_eur for {bad}")
    oid = f"order-{uuid.uuid4().hex[:12]}"
    _audit(oid, "buyer-agent", "order.requested", req.model_dump(mode="json"),
           f"{mandate.buyer_name} requested {req.need.quantity:g} {req.need.unit}: {req.need.text}")
    res = evaluate(JuryRequest(mandate=mandate, need=req.need, offers=req.lines))
    audit_verdicts(oid, res.verdicts)
    status = STATUS[res.decision]
    subs = substitutes(SubstitutesRequest(mandate=mandate, need=req.need, offer=req.lines[0])) \
        if status == "substitutes_proposed" else []
    return finalize(oid, mandate, req.need, req.lines, res.verdicts, status, subs)


def audit_verdicts(oid: str, verdicts: list[Verdict]) -> None:
    for v in verdicts:
        _audit(oid, f"juror:{v.juror}", f"verdict.{v.decision}", v.model_dump(mode="json"),
               f"{v.juror} juror says {v.decision} ({v.confidence:.0%}): {'; '.join(v.reasons)}")


def finalize(oid: str, mandate: Mandate, need: Need, lines: list[Offer], verdicts: list[Verdict],
             status: str, subs: list[Offer], reason: str | None = None) -> Order:
    """Persist the decided order and write its closing order.<status> audit event (shared by POST /orders
    and the need-based flow)."""
    o = Order(id=oid, mandate_id=mandate.id, need=need, lines=lines, verdicts=verdicts, status=status,
              confirmation_no=_confirm() if status == "placed" else None, substitutes=subs,
              created_at=datetime.now(UTC))
    get_order_store().save(o)
    why = f". {reason}" if reason else ""
    _audit(oid, "aimo", f"order.{status}", {"status": status, "confirmation_no": o.confirmation_no},
           {"placed": f"Auto-ordered {_what(o)}, confirmation {o.confirmation_no}",
            "pending_approval": f"Held {_what(o)} for human approval{why}",
            "substitutes_proposed": f"Proposed {len(o.substitutes)} substitutes for {_what(o)}{why}",
            "rejected": f"Rejected {_what(o)}{why}"}[status])
    return o


def get(order_id: str) -> Order:
    o = get_order_store().get(order_id)
    if o is None:
        raise HTTPException(404, f"unknown order {order_id!r}")
    return o


def _decide(order_id: str, allowed: set[str], new: str, verb: str, reason: str | None = None,
            accepting_ok: bool = False) -> Order:
    with _lock:
        o = get(order_id)
        if order_id in _accepting and not accepting_ok:
            raise HTTPException(409, "a substitute is being ordered for this order; try again in a moment")
        if o.status not in allowed:
            raise HTTPException(409, f"cannot {verb} an order that is {o.status}; "
                                     f"allowed from: {', '.join(sorted(allowed))}")
        o.status = new
        o.confirmation_no = _confirm() if new == "placed" else None
        get_order_store().save(o)
        done = verb.rstrip("e") + "ed"  # approve -> approved, reject -> rejected
        payload = {"status": new, "confirmation_no": o.confirmation_no} if reason is None else {"status": new, "reason": reason}
        _audit(order_id, "human", f"order.{done}", payload,
               f"Human {done} {_what(o)}" + (f", confirmation {o.confirmation_no}" if o.confirmation_no else "")
               + (f". Reason: {reason}" if reason else ""))
        return o


def approve(order_id: str) -> Order:
    return _decide(order_id, {"pending_approval"}, "placed", "approve")


def reject(order_id: str, reason: str | None = None) -> Order:
    return _decide(order_id, {"pending_approval", "substitutes_proposed"}, "rejected", "reject", reason)


def accept_substitute(order_id: str, product_id: str) -> Order:
    """Buyer picks one proposed substitute: a new order for it goes through the jury again,
    then the original is closed with a reason that links the new order (both audited)."""
    with _lock:
        o = get(order_id)
        if o.status != "substitutes_proposed" or order_id in _accepting:
            raise HTTPException(409, f"cannot accept a substitute for an order that is {o.status}")
        sub = next((s for s in o.substitutes if s.product_id == product_id), None)
        if sub is None:
            raise HTTPException(404, f"{product_id!r} is not a proposed substitute of {order_id}")
        _accepting.add(order_id)
    try:
        new = create(OrderRequest(mandate_id=o.mandate_id, need=o.need, lines=[sub]))  # original untouched if this fails
        _decide(order_id, {"substitutes_proposed"}, "rejected", "reject",
                f"Replaced by substitute {sub.name} in {new.id}", accepting_ok=True)
        return new
    finally:
        with _lock:
            _accepting.discard(order_id)
