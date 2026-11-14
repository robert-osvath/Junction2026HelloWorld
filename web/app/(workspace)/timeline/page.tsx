"use client";
// Not /audit: that path is proxied to the API (next.config.ts API_PATHS) and a page there would shadow it.
import { useState } from "react";
import { dt, shortId, who, type AuditEvent, type AuditVerify, type Order } from "../../api";
import { usePoll } from "../../components/hooks";
import Icon, { type IconName } from "../../components/Icon";

function look(e: AuditEvent): [IconName, string] {
  const tone = /reject/.test(e.action) ? "peach" : /escalate|pending|substitut/.test(e.action) ? "cream" : /approve|placed/.test(e.action) ? "lime" : "green";
  if (e.actor === "buyer-agent") return ["send", tone];
  if (e.actor.startsWith("juror:")) return ["users", tone];
  if (e.actor === "human") return ["check", tone];
  return ["shield", tone];
}

export default function Timeline() {
  const [orderId, setOrderId] = useState("");
  const orders = usePoll<Order[]>("/orders", 15_000);
  const events = usePoll<AuditEvent[]>(orderId ? `/audit?order_id=${encodeURIComponent(orderId)}` : "/audit", 15_000);
  const verify = usePoll<AuditVerify>("/audit/verify", 30_000);
  const list = [...(events.data ?? [])].sort((a, b) => b.ts.localeCompare(a.ts));
  const selected = orders.data?.find((o) => o.id === orderId);

  function exportLog() {
    const blob = new Blob([JSON.stringify(list, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `aimo-audit-${orderId ? shortId(orderId) : "all"}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <>
      <section className="page-heading">
        <div>
          <p className="eyebrow">Full traceability</p>
          <h1>Audit timeline</h1>
          <p>Every request, juror verdict and human decision, newest first.</p>
        </div>
      </section>
      {events.err && <p className="err" role="alert">Backend: {events.err}</p>}

      <div className="audit-layout">
        <div className="audit-panel">
          <div className="audit-filter">
            <label htmlFor="order-filter">Showing activity for</label>
            <select id="order-filter" value={orderId} onChange={(e) => setOrderId(e.target.value)}>
              <option value="">All orders</option>
              {(orders.data ?? []).map((o) => <option key={o.id} value={o.id}>{shortId(o.id)} · {o.need.text}</option>)}
            </select>
          </div>
          {events.data === null ? <p className="empty-state">Loading…</p> : list.length === 0 ? <p className="empty-state">No events yet.</p> : (
            <ol className="event-list">
              {list.map((e) => {
                const [icon, tone] = look(e);
                return (
                  <li className="timeline-event" key={e.id}>
                    <span className={`timeline-icon ${tone}`} aria-hidden="true"><Icon name={icon} /></span>
                    <time dateTime={e.ts}>{dt(e.ts)}</time>
                    <div>
                      <h3>{who(e.actor)}{e.order_id && !orderId ? <span className="muted"> · {shortId(e.order_id)}</span> : null}</h3>
                      <p>{e.human_summary}</p>
                      <details><summary>Technical details</summary><code>{e.action}<br />{e.hash}</code></details>
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </div>

        <aside className="audit-trust">
          <span className="shield"><Icon name="shield" size={28} /></span>
          <h3>{verify.data === null ? "Checking the chain…" : verify.data.ok ? "Audit log verified" : "Audit log broken"}</h3>
          <p>{verify.data === null ? "" : verify.data.ok
            ? `${verify.data.checked} entries checked. Each entry hashes the one before it, so no entry can be edited unseen.`
            : `The hash chain breaks at entry ${verify.data.broken_at}.`}</p>
          <hr />
          <span>Order</span>
          <strong>{selected ? selected.id : "All orders"}</strong>
          {selected?.created_at && <><span>Created</span><strong>{dt(selected.created_at)}</strong></>}
          <span>Events shown</span>
          <strong>{list.length}</strong>
          <button className="secondary" onClick={exportLog} disabled={list.length === 0}><Icon name="file" />Export audit log</button>
        </aside>
      </div>
    </>
  );
}
