"""Need-based ordering: the buyer states a need, the jury picks the best product among candidates.

Candidates (catalog search under the mandate's hard rules) are priced live, then the jury judges EACH
alternative on its own (`aggregate_verdicts` treats offers as lines of one order, so it is called once
per alternative, never with all of them). Ranking of acceptable alternatives:
  jury decision (auto_order before human_approval) -> cheapest total_eur -> highest mean juror confidence.
Product-type fit (is this really what the buyer meant by "milk"?) is the jury's, i.e. the spec juror's,
responsibility: the ranking never second-guesses it.
A substitution only happens when NO alternative meets need + policy: then near alternatives are
proposed for approval. Banned allergens are never relaxed.
"""
import statistics
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from app.catalog import store
from app.catalog.aimo import get_client
from app.catalog.offers import get_offers
from app.catalog.search import search
from app.jury.catalog_lookup import resolve_catalog_items
from app.jury.router import evaluate, substitutes
from app.orders.service import STATUS, _audit, audit_verdicts, finalize
from contracts import (JuryRequest, JuryResult, Mandate, Need, Offer, OffersRequest, Order,
                       SubstitutesRequest)

MAX_WORKERS = 5


class NoOffers(Exception):
    """A product hint produced no offer at all (and `strict_hint` was set)."""


@dataclass
class Alternative:
    offer: Offer
    result: JuryResult
    acceptable: bool
    note: str = ""

    @property
    def mean_confidence(self) -> float:
        v = self.result.verdicts
        return statistics.fmean(x.confidence for x in v) if v else 0.0

    def rank_key(self) -> tuple:
        return (self.result.decision != "auto_order", self.offer.total_eur, -self.mean_confidence)


def _candidate_ids(conn, mandate: Mandate, need: Need, product_hint: str | None, limit: int) -> list[str]:
    claims = ",".join(mandate.required_claims)
    banned = ",".join(mandate.banned_allergens)
    ids = [i.product_id for i in search(conn, need.product_intent, claims, banned, "", limit,
                                        need.quantity, need.unit)]
    if product_hint and product_hint not in ids:
        # a hint only joins the shortlist if it passes the same hard filters; it never forces the choice
        if store.rows_by_ids(conn, [product_hint], list(mandate.required_claims),
                             list(mandate.banned_allergens), ""):
            ids.insert(0, product_hint)
    return ids


def _judge(mandate: Mandate, need: Need, offer: Offer) -> Alternative:
    res = evaluate(JuryRequest(mandate=mandate, need=need, offers=[offer]))
    if mandate.strict_allergens and offer.evidence.allergens == "missing":
        return Alternative(offer, res, False, "allergen data missing and the policy is strict")
    ok = res.decision in ("auto_order", "human_approval")
    return Alternative(offer, res, ok, "" if ok else _why(res))


def _why(res: JuryResult) -> str:
    reasons = [r for v in res.verdicts if v.decision != "approve" for r in v.reasons]
    return "; ".join(reasons) or res.decision


def _comparison_summary(alts: list[Alternative], best: Alternative | None) -> str:
    rows = "; ".join(f"{a.offer.name} {a.offer.total_eur:,.2f} EUR -> {'not acceptable' if not a.acceptable else a.result.decision}"
                     for a in alts)
    if best is None:
        return f"Compared {len(alts)} alternatives, none meets the need and policy: {rows}"
    why = ("auto-orderable and the cheapest" if best.result.decision == "auto_order"
           else "no alternative could be ordered automatically; cheapest that needs approval")
    return f"Compared {len(alts)} alternatives: {rows}. Winner {best.offer.name}: {why}."


def order_from_need(mandate: Mandate, need: Need, *, product_hint: str | None = None,
                    simulate_out_of_stock: bool = False, max_candidates: int = 8,
                    strict_hint: bool = False) -> Order:
    """Place (or escalate) an order for a need. With `strict_hint`, a hint that yields no offers at all
    raises NoOffers before anything is persisted (the caller explains why). `simulate_out_of_stock` is an explicit demo switch: it marks
    every candidate offer sales-blocked / no stock before judging, to exercise the substitute path."""
    conn = store.connect()
    try:
        ids = _candidate_ids(conn, mandate, need, product_hint, max_candidates)
        offers = get_offers(conn, get_client(), OffersRequest(need=need, product_ids=ids,
                                                              delivery_date=need.deliver_by)) if ids else []
    finally:
        conn.close()
    if strict_hint and product_hint and not offers:
        raise NoOffers(product_hint)  # decided before anything is persisted or audited
    oid = f"order-{uuid.uuid4().hex[:12]}"
    _audit(oid, "buyer-agent", "order.requested",
           {"mandate_id": mandate.id, "need": need.model_dump(mode="json"), "product_hint": product_hint,
            "simulate_out_of_stock": simulate_out_of_stock},
           f"{mandate.buyer_name} requested {need.quantity:g} {need.unit}: {need.text}")
    if simulate_out_of_stock:
        offers = [o.model_copy(update={"sales_block": True,
                                       "evidence": o.evidence.model_copy(update={"stock": "none"})})
                  for o in offers]
    if offers:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(offers))) as pool:
            alts = list(pool.map(lambda o: _judge(mandate, need, o), offers))
    else:
        alts = []
    acceptable = sorted((a for a in alts if a.acceptable), key=Alternative.rank_key)
    best = acceptable[0] if acceptable else None
    if alts:
        _audit(oid, "aimo", "jury.compared",
               {"alternatives": [{"product_id": a.offer.product_id, "name": a.offer.name,
                                  "total_eur": a.offer.total_eur, "decision": a.result.decision,
                                  "acceptable": a.acceptable, "note": a.note} for a in alts],
                "winner": best.offer.product_id if best else None},
               _comparison_summary(alts, best))
    if best is not None:
        audit_verdicts(oid, best.result.verdicts)
        status = STATUS[best.result.decision]
        reason = None
        if status == "pending_approval":
            reason = "; ".join(f"{v.juror}: {', '.join(v.reasons)}"
                               for v in best.result.verdicts if v.decision != "approve") or None
        return finalize(oid, mandate, need, [best.offer], best.result.verdicts, status, [], reason)
    if not alts:
        reason = f"No product matches {need.product_intent} under the policy"
        return finalize(oid, mandate, need, [], [], "rejected", [], reason)
    # nothing meets need + policy: the best failing offer anchors the substitute search
    failing = min(alts, key=lambda a: (a.result.decision != "substitutes", a.offer.total_eur))
    audit_verdicts(oid, failing.result.verdicts)
    reasons = "; ".join(f"{a.offer.name}: {a.note}" for a in alts)
    if mandate.substitutes_allowed:
        subs = _safe_substitutes(substitutes(SubstitutesRequest(mandate=mandate, need=need, offer=failing.offer)),
                                 mandate)
        return finalize(oid, mandate, need, [failing.offer], failing.result.verdicts, "substitutes_proposed",
                        subs, f"No product meets the need and policy ({reasons})")
    return finalize(oid, mandate, need, [failing.offer], failing.result.verdicts, "rejected", [],
                    f"No product meets the need and policy and substitutes are not allowed ({reasons})")


def _safe_substitutes(subs: list[Offer], mandate: Mandate) -> list[Offer]:
    """Never relax banned allergens, even if the ranker let one through."""
    if not mandate.banned_allergens or not subs:
        return subs
    banned = set(mandate.banned_allergens)
    items = resolve_catalog_items([s.product_id for s in subs])
    out = []
    for s in subs:
        item = items.get(s.product_id)
        if item is not None and (banned & set(item.allergens_contains)
                                 or (mandate.strict_allergens and banned & set(item.allergens_may_contain))):
            continue
        out.append(s)
    return out
