"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { ago, api, eur, heldReason, LABEL, total, type Order, type Status } from "../../api";

const TABS = Object.keys(LABEL) as Status[];

export default function Page() {
  return <Suspense fallback={<p>Loading...</p>}><Inbox /></Suspense>;
}

function Inbox() {
  const router = useRouter();
  const initial = useSearchParams().get("status") as Status | null;  // deep links, e.g. from the cockpit's metric cards
  const [tab, setTab] = useState<Status>(initial && Object.hasOwn(LABEL, initial) ? initial : "pending_approval");
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    let live = true;
    const load = () => api<Order[]>("/orders")
      .then((o) => live && (setOrders(o), setErr("")))
      .catch((e) => live && (setOrders((p) => p ?? []), setErr(e.message)));
    void load();
    const t = setInterval(load, 10_000);
    return () => { live = false; clearInterval(t); };
  }, []);

  const count = (s: Status) => (orders ?? []).filter((o) => o.status === s).length;
  const rows = (orders ?? []).filter((o) => o.status === tab);

  return (
    <>
      <section className="page-heading"><div>
        <p className="eyebrow">{count("pending_approval")} waiting for you</p>
        <h1>Approval inbox</h1>
        <p>Review what the jury held, approve or reject, and check the audit trail.</p>
      </div></section>
      {err && <p className="err" role="alert">{err}</p>}
      <div className="table-panel">
        <div className="table-tools">
          <nav className="tabs" aria-label="Order status">
            {TABS.map((t) => (
              <button key={t} aria-current={t === tab} onClick={() => setTab(t)}>{LABEL[t]} <span>{count(t)}</span></button>
            ))}
          </nav>
        </div>
        {orders === null ? <p className="empty-state">Loading...</p> : rows.length === 0 ? <p className="empty-state">No orders with status {LABEL[tab].toLowerCase()}.</p> : (
          <div className="table-wrap"><table>
            <thead>
              <tr>
                <th scope="col">Order</th><th scope="col">Buyer</th><th scope="col">Product</th><th scope="col">Quantity</th>
                <th scope="col" className="num">Total</th><th scope="col">Reason held</th><th scope="col">Waiting since</th><th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((o) => (
                <tr key={o.id} className="click" onClick={() => router.push(`/approvals/${o.id}`)}>
                  <td><Link href={`/approvals/${o.id}`} onClick={(e) => e.stopPropagation()}>{o.id.replace("order-", "").slice(0, 8)}</Link></td>
                  <td>{o.mandate_id}</td>
                  <td>{o.lines[0]?.name}{o.lines.length > 1 && ` +${o.lines.length - 1} more`}</td>
                  <td>{o.lines[0] && `${o.lines[0].units_needed} x ${o.lines[0].sales_unit}`}</td>
                  <td className="num">{eur(total(o))}</td>
                  <td>{heldReason(o)}</td>
                  <td>{ago(o.created_at)}</td>
                  <td><span className={`status s-${o.status}`}>{LABEL[o.status]}</span></td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </div>
    </>
  );
}
