"use client";
import { useCallback, useEffect, useState } from "react";
import { api, eur, type AuditEvent, type AuditVerify, type Evidence, type Offer, type Order } from "../../api";

const STOCK = { verified: "good", estimated: "warn", none: "bad" } as const;

function Badges({ e }: { e: Evidence }) {
  return (
    <>
      <span className={`badge ${STOCK[e.stock]}`}>stock {e.stock}</span>{" "}
      <span className={`badge ${e.allergens === "declared" ? "good" : "warn"}`}>allergens {e.allergens}</span>{" "}
      <span className={`badge ${e.reliability === null ? "warn" : "good"}`}>
        {e.reliability === null ? "no data" : `reliability ${Math.round(e.reliability * 100)}%`}
      </span>
    </>
  );
}

function Lines({ lines }: { lines: Offer[] }) {
  return (
    <table>
      <thead><tr><th>Product</th><th>Units</th><th>Total</th><th>Evidence</th><th>Sales block</th><th>Closing</th><th>Available</th></tr></thead>
      <tbody>
        {lines.map((l) => (
          <tr key={l.product_id}>
            <td>{l.name}</td><td>{l.units_needed} x {l.sales_unit}</td><td>{eur(l.total_eur)}</td>
            <td><Badges e={l.evidence} /></td>
            <td>{l.sales_block ? "blocked" : "no"}</td>
            <td>{l.closing_time ? new Date(l.closing_time).toLocaleString() : "n/a"}</td>
            <td>{l.available_date}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function OrderView({ id }: { id: string }) {
  const [order, setOrder] = useState<Order | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [chain, setChain] = useState<AuditVerify | null>(null);
  const [err, setErr] = useState("");

  const load = useCallback(
    () =>
      Promise.all([api<Order>(`/orders/${id}`), api<AuditEvent[]>(`/audit?order_id=${id}`), api<AuditVerify>("/audit/verify")])
        .then(([o, ev, v]) => { setOrder(o); setEvents(ev); setChain(v); })
        .catch((e) => setErr(e.message)),
    [id],
  );

  useEffect(() => { void load(); }, [load]);

  async function act(kind: "approve" | "reject") {
    setErr("");
    try { await api(`/orders/${id}/${kind}`, "POST"); } catch (e) { setErr((e as Error).message); }
    await load();
  }

  if (!order) return err ? <p className="err">{err}</p> : <p>Loading...</p>;
  const canApprove = order.status === "pending_approval";
  const canReject = canApprove || order.status === "substitutes_proposed";

  return (
    <>
      <h1>{order.mandate_id} <span className={`badge ${order.status}`}>{order.status}</span></h1>
      <p>{order.need.text}{order.confirmation_no && <> | confirmation {order.confirmation_no}</>}</p>
      {err && <p className="err">{err}</p>}
      <div className="actions">
        {canApprove && <button className="approve" onClick={() => act("approve")}>Approve</button>}
        {canReject && <button className="reject" onClick={() => act("reject")}>Reject</button>}
      </div>

      <h2>Lines</h2>
      <Lines lines={order.lines} />

      <h2>Juror verdicts</h2>
      <div className="cards">
        {order.verdicts.map((v, i) => (
          <div key={i} className={`card ${v.decision}`}>
            <strong>{v.juror}</strong> <span className="badge">{v.decision}</span> {Math.round(v.confidence * 100)}%
            {v.model && <small> ({v.model})</small>}
            <ul>{v.reasons.map((r, j) => <li key={j}>{r}</li>)}</ul>
          </div>
        ))}
      </div>

      {order.substitutes.length > 0 && (<><h2>Substitutes</h2><Lines lines={order.substitutes} /></>)}

      <h2>Timeline {chain && <span className={`badge ${chain.ok ? "good" : "bad"}`}>
        {chain.ok ? `chain verified (${chain.checked})` : `chain broken at ${chain.broken_at}`}</span>}</h2>
      <table>
        <thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Summary</th><th>Hash</th></tr></thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id}>
              <td>{new Date(e.ts).toLocaleString()}</td><td>{e.actor}</td><td>{e.action}</td>
              <td>{e.human_summary}</td><td><code>{e.hash.replace("sha256:", "").slice(0, 8)}</code></td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
