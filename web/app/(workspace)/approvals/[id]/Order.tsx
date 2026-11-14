"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import {
  api, dt, eur, heldReason, LABEL, total,
  type AuditEvent, type AuditVerify, type Offer, type Order, type RejectRequest,
} from "../../../api";

const RESULT = { approve: "Approve", escalate: "Escalate", reject: "Reject" } as const;

function who(a: string) {
  if (a === "buyer-agent") return "Buyer agent";
  if (a === "aimo") return "Aimo system";
  if (a === "human") return "Approver";
  return a.startsWith("juror:") ? `Juror: ${a.slice(6).replace(/^./, (c) => c.toUpperCase())}` : a;
}

function Lines({ lines }: { lines: Offer[] }) {
  const [now] = useState(() => Date.now());
  return (
    <div className="table-wrap"><table>
      <thead>
        <tr>
          <th scope="col">Product</th><th scope="col">Quantity</th><th scope="col" className="num">Unit price</th>
          <th scope="col" className="num">Total</th><th scope="col">Available</th><th scope="col">Order cut-off</th><th scope="col">Evidence</th>
        </tr>
      </thead>
      <tbody>
        {lines.map((l) => {
          const e = l.evidence;
          const passed = l.closing_time !== null && new Date(l.closing_time).getTime() < now;
          return (
            <tr key={l.product_id}>
              <td>{l.name}</td>
              <td>{l.units_needed} x {l.sales_unit}</td>
              <td className="num">{eur(l.unit_price_eur)}</td>
              <td className="num">{eur(l.total_eur)}</td>
              <td>{l.available_date}</td>
              <td>
                {l.closing_time ? dt(l.closing_time) : "—"}
                {passed && <p className="note">Order cut-off passed ({dt(l.closing_time!)})</p>}
                {l.sales_block && <p className="note error">Sales blocked</p>}
              </td>
              <td>
                <ul className="plain">
                  <li>Stock: {e.stock === "estimated" ? "estimated (supplier-delivered, quantity unknown)" : e.stock}</li>
                  <li>Allergens: {e.allergens}</li>
                  <li>Delivery reliability: {e.reliability === null ? "no data" : `${Math.round(e.reliability * 100)}%`}</li>
                </ul>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table></div>
  );
}

export default function OrderView({ id }: { id: string }) {
  const [order, setOrder] = useState<Order | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [chain, setChain] = useState<AuditVerify | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const router = useRouter();

  const load = useCallback(
    () =>
      Promise.all([api<Order>(`/orders/${id}`), api<AuditEvent[]>(`/audit?order_id=${id}`), api<AuditVerify>("/audit/verify")])
        .then(([o, ev, v]) => { setOrder(o); setEvents(ev); setChain(v); })
        .catch((e) => setErr(e.message)),
    [id],
  );

  useEffect(() => { void load(); }, [load]);

  async function acceptSubstitute(product_id: string) {
    setErr("");
    setBusy(true);
    try {
      const next = await api<Order>(`/orders/${id}/accept-substitute`, "POST", { product_id });
      router.push(`/approvals/${next.id}`);  // the substitute is a new order with its own jury verdicts
    } catch (e) { setErr((e as Error).message); setBusy(false); }
  }

  async function act(path: "approve" | "reject", body?: RejectRequest) {
    setErr("");
    setBusy(true);  // no double submit while the request is in flight
    try {
      await api(`/orders/${id}/${path}`, "POST", body);
      setRejecting(false);
      setReason("");
    } catch (e) { setErr((e as Error).message); }
    await load();
    setBusy(false);
  }

  const crumbs = <p className="crumbs"><Link href="/approvals">Order approvals</Link> / {id}</p>;
  if (!order) return <>{crumbs}{err ? <p className="err">{err}</p> : <p className="panel muted">Loading...</p>}</>;
  const canApprove = order.status === "pending_approval";
  const canReject = canApprove || order.status === "substitutes_proposed";

  return (
    <>
      {crumbs}
      <section className="hero">
        <p className="eyebrow">Order {id.replace("order-", "").slice(0, 8)}</p>
        <h1>{order.need.text}</h1>
      </section>

      <section className="panel stack" aria-labelledby="h-decision">
        <div className="panel-heading">
          <div><p className="eyebrow">Decision</p><h2 id="h-decision">{canApprove ? "Awaiting your decision" : "Order summary"}</h2></div>
          <span className={`status s-${order.status}`}>{LABEL[order.status]}</span>
        </div>
        <dl>
          <dt>Order</dt><dd>{id}</dd>
          <dt>Buyer</dt><dd>{order.mandate_id}</dd>
          <dt>Request</dt><dd>{order.need.text}</dd>
          <dt>Total</dt><dd>{eur(total(order))}</dd>
          <dt>Reason held</dt><dd>{heldReason(order)}</dd>
          {order.confirmation_no && <><dt>Confirmation</dt><dd>{order.confirmation_no}</dd></>}
        </dl>
        {err && <p className="err" role="alert">{err}</p>}
        {rejecting ? (
          <form onSubmit={(e) => { e.preventDefault(); void act("reject", { reason: reason.trim() }); }}>
            <label htmlFor="reason">Reason for rejection</label>
            <textarea id="reason" required maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
            <div className="row">
              <button type="button" className="secondary" disabled={busy} onClick={() => setRejecting(false)}>Cancel</button>
              <button type="submit" className="danger-button" disabled={busy || !reason.trim()}>Confirm reject</button>
            </div>
          </form>
        ) : (
          <div className="row">
            {canApprove && <button className="primary" disabled={busy} onClick={() => void act("approve")}>Approve</button>}
            {canReject && <button className="secondary" disabled={busy} onClick={() => setRejecting(true)}>Reject</button>}
          </div>
        )}
      </section>

      <section className="panel stack" aria-labelledby="h-lines">
        <div className="panel-heading"><div><p className="eyebrow">Evidence</p><h2 id="h-lines">Order lines</h2></div></div>
        <Lines lines={order.lines} />
      </section>

      <section className="panel stack" aria-labelledby="h-jury">
        <div className="panel-heading"><div><p className="eyebrow">Jury</p><h2 id="h-jury">Jury assessment</h2></div></div>
        <div className="table-wrap"><table>
          <thead>
            <tr><th scope="col">Juror</th><th scope="col">Result</th><th scope="col" className="num">Confidence</th><th scope="col">Reasons</th><th scope="col">Model</th></tr>
          </thead>
          <tbody>
            {order.verdicts.map((v, i) => (
              <tr key={i}>
                <td>{v.juror}</td>
                <td className={`r-${v.decision}`}><span aria-hidden="true" className="mark">{v.decision === "approve" ? "✓" : v.decision === "reject" ? "✕" : "!"}</span> {RESULT[v.decision]}</td>
                <td className="num">{Math.round(v.confidence * 100)}%</td>
                <td><ul className="bul">{v.reasons.map((r, j) => <li key={j}>{r}</li>)}</ul></td>
                <td>{v.model ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </section>

      {order.substitutes.length > 0 && (
        <section className="panel stack" aria-labelledby="h-subs">
          <div className="panel-heading"><div><p className="eyebrow">Alternatives</p><h2 id="h-subs">Substitutes</h2></div></div>
          <Lines lines={order.substitutes} />
          {order.status === "substitutes_proposed" && (
            <div className="row wrap">
              {order.substitutes.map((s) => (
                <button key={s.product_id} className="primary" disabled={busy} onClick={() => void acceptSubstitute(s.product_id)}>
                  Order {s.name} ({eur(s.total_eur)})
                </button>
              ))}
            </div>
          )}
          {order.status === "substitutes_proposed" && <p className="muted">Ordering a substitute creates a new order that the jury checks again; this one is then closed.</p>}
        </section>
      )}

      <section className="panel stack" aria-labelledby="h-audit">
        <div className="panel-heading"><div><p className="eyebrow">Audit</p><h2 id="h-audit">Audit trail</h2></div></div>
        {chain && (chain.ok
          ? <p className="status-line">Audit log verified — {chain.checked} entries, unchanged</p>
          : <p className="status-line bad">Audit log broken at {chain.broken_at}</p>)}
        <ol className="timeline">
          {events.map((e) => (
            <li key={e.id}>
              <time dateTime={e.ts}>{dt(e.ts)}</time>
              <strong>{who(e.actor)}</strong>
              {e.human_summary}
              <details><summary>Show technical details</summary><code>{e.action}<br />{e.hash}</code></details>
            </li>
          ))}
        </ol>
      </section>
    </>
  );
}
