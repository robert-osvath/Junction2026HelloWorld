"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, dt, eur, LABEL, productName, total, WAITING, type Order, type Verdict } from "../../api";
import { useNow, usePoll } from "../../components/hooks";
import Icon from "../../components/Icon";
import Modal from "../../components/Modal";

const RESULT = { approve: "Approve", escalate: "Escalate", reject: "Reject" } as const;
const jurorName = (j: string) => j.replace(/^tier(\d)$/, "Tier $1").replace(/^./, (c) => c.toUpperCase());
const approved = (o: Order) => o.verdicts.filter((v) => v.decision === "approve").length;
const share = (o: Order) => (o.verdicts.length ? Math.round((approved(o) / o.verdicts.length) * 100) : 0);

function Verdicts({ order, onClose }: { order: Order; onClose: () => void }) {
  return (
    <Modal title="Jury verdicts" subtitle={`${order.need.text} · ${order.lines[0]?.name ?? "no product"}`} onClose={onClose}>
      <ul className="score-list">
        {order.verdicts.length === 0 && <li className="score-row"><span className="muted">No verdicts recorded for this order.</span></li>}
        {order.verdicts.map((v: Verdict, i) => (
          <li className="score-row" key={i}>
            <div>
              <strong>{jurorName(v.juror)}</strong>
              <ul className="bul">{v.reasons.map((r, j) => <li key={j}>{r}</li>)}</ul>
              {v.model && <small>Model: {v.model}</small>}
            </div>
            <div className={`verdict v-${v.decision}`}><b>{RESULT[v.decision]}</b><small>{Math.round(v.confidence * 100)}% confident</small></div>
          </li>
        ))}
      </ul>
      <div className="modal-actions">
        <Link className="secondary" href={`/approvals/${order.id}`}>Open full record</Link>
        <button className="primary" onClick={onClose}>Done</button>
      </div>
    </Modal>
  );
}

export default function Jury() {
  const router = useRouter();
  const now = useNow();
  const orders = usePoll<Order[]>("/orders");
  const [open, setOpen] = useState<Order | null>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");

  const all = [...(orders.data ?? [])].sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
  const feature = all.find((o) => WAITING.includes(o.status)) ?? all[0];
  const rest = all.filter((o) => o !== feature).slice(0, 9);
  const cutoff = feature?.lines[0]?.closing_time;
  const hoursLeft = cutoff && now ? Math.round((new Date(cutoff).getTime() - now) / 3_600_000) : null;

  async function accept(o: Order, product_id: string) {
    setBusy(product_id); setErr("");
    try {
      const next = await api<Order>(`/orders/${o.id}/accept-substitute`, "POST", { product_id });
      router.push(`/approvals/${next.id}`);  // the substitute is a new order with its own verdicts
    } catch (e) { setErr((e as Error).message); setBusy(""); }
  }

  return (
    <>
      <section className="page-heading">
        <div>
          <p className="eyebrow">Automated evaluation</p>
          <h1>Jury decisions</h1>
          <p>Code gates and model jurors check every order. See why each one was placed, held or answered with substitutes.</p>
        </div>
      </section>
      {(orders.err || err) && <p className="err" role="alert">{err || `Backend: ${orders.err}`}</p>}

      {orders.loading ? <p className="empty-state">Loading…</p> : !feature ? (
        <p className="empty-state"><Icon name="users" />No orders yet. Start a purchase and the jury will evaluate it here.</p>
      ) : (
        <div className="jury-feature">
          <div>
            <span className={`tag ${feature.status === "substitutes_proposed" ? "jury-tag" : "urgent"}`}>{WAITING.includes(feature.status) ? "Needs your decision" : LABEL[feature.status]}</span>
            <h2>{feature.need.text}</h2>
            <p>{feature.lines[0]?.name ?? "No product"} · {eur(total(feature))} · {feature.mandate_id}</p>
            <div className="jury-meta">
              <span><Icon name="users" />{feature.verdicts.length} verdicts</span>
              <span><Icon name="file" />{feature.lines.length} order {feature.lines.length === 1 ? "line" : "lines"}</span>
              {cutoff && <span><Icon name="clock" />{hoursLeft === null ? `Cut-off ${dt(cutoff)}` : hoursLeft > 0 ? `Order cut-off in ${hoursLeft} h` : `Cut-off passed (${dt(cutoff)})`}</span>}
            </div>
            <div className="row-actions">
              <button className="primary" onClick={() => setOpen(feature)}>See the verdicts <Icon name="arrow" /></button>
              {feature.status === "pending_approval" && <Link className="ghost-button" href="/approvals?status=pending_approval">Go to approval</Link>}
              {feature.status === "substitutes_proposed" && feature.substitutes[0] && (
                <button className="ghost-button" disabled={busy !== ""} onClick={() => void accept(feature, feature.substitutes[0].product_id)}>
                  {busy === feature.substitutes[0].product_id ? "Ordering…" : `Order ${productName(feature.substitutes[0].name)} (${eur(feature.substitutes[0].total_eur)})`}
                </button>
              )}
              {feature.status === "substitutes_proposed" && feature.substitutes.length > 1 && <Link className="ghost-button" href={`/approvals/${feature.id}`}>All {feature.substitutes.length} substitutes</Link>}
            </div>
          </div>
          <div className="score-ring" style={{ background: `conic-gradient(var(--lime) ${share(feature)}%, rgba(255, 255, 255, 0.14) 0)` }}>
            <div><strong>{approved(feature)}/{feature.verdicts.length}</strong><span>jurors approve</span></div>
          </div>
        </div>
      )}

      {rest.length > 0 && <div className="section-heading"><h2>Other decisions</h2><Link className="text-button" href="/sourcing">All orders <Icon name="arrow" size={15} /></Link></div>}
      <div className="card-grid">
        {rest.map((o) => {
          const sub = o.status === "substitutes_proposed" ? o.substitutes[0] : undefined;
          return (
            <article className="decision-card" key={o.id}>
              <div className="card-top"><span className={`status s-${o.status}`}>{LABEL[o.status]}</span><small>{eur(total(o))}</small></div>
              <h3>{o.need.text}</h3>
              <p>{o.lines[0]?.name ?? "No product"}</p>
              <div className="juror-row" aria-label={`${approved(o)} of ${o.verdicts.length} jurors approve`}>
                <div>{o.verdicts.map((v, i) => <span key={i} className={`avatar v-${v.decision}`} title={`${jurorName(v.juror)}: ${RESULT[v.decision]}`} aria-hidden="true">{jurorName(v.juror)[0]}</span>)}</div>
                <strong>{approved(o)}/{o.verdicts.length} approve</strong>
              </div>
              <div className="progress"><span style={{ width: `${share(o)}%` }} /></div>
              {sub && (
                <div className="winner">
                  <span><Icon name="check" /></span>
                  <div><small>Top substitute</small><strong>{productName(sub.name)}</strong></div>
                </div>
              )}
              {sub
                ? <button className="primary" disabled={busy !== ""} onClick={() => void accept(o, sub.product_id)}>{busy === sub.product_id ? "Ordering…" : `Order substitute (${eur(sub.total_eur)})`}</button>
                : <button className="secondary" onClick={() => setOpen(o)}>See the verdicts</button>}
            </article>
          );
        })}
      </div>
      {open && <Verdicts order={open} onClose={() => setOpen(null)} />}
    </>
  );
}
