"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ago, api, eur, heldReason, LABEL, total, type Order, type Status } from "./api";

const TABS = Object.keys(LABEL) as Status[];

export default function Inbox() {
  const router = useRouter();
  const [tab, setTab] = useState<Status>("pending_approval");
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
      <h1>Order approvals</h1>
      <nav className="tabs" aria-label="Order status">
        {TABS.map((t) => (
          <button key={t} aria-current={t === tab} onClick={() => setTab(t)}>{LABEL[t]} ({count(t)})</button>
        ))}
      </nav>
      {err && <p className="err">{err}</p>}
      {orders === null ? <p>Loading...</p> : rows.length === 0 ? <p className="muted">No orders with status {LABEL[tab].toLowerCase()}.</p> : (
        <table>
          <thead>
            <tr>
              <th scope="col">Order</th><th scope="col">Buyer</th><th scope="col">Product</th><th scope="col">Quantity</th>
              <th scope="col" className="num">Total</th><th scope="col">Reason held</th><th scope="col">Waiting since</th><th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((o) => (
              <tr key={o.id} className="click" onClick={() => router.push(`/orders/${o.id}`)}>
                <td><Link href={`/orders/${o.id}`} onClick={(e) => e.stopPropagation()}>{o.id.replace("order-", "").slice(0, 8)}</Link></td>
                <td>{o.mandate_id}</td>
                <td>{o.lines[0]?.name}{o.lines.length > 1 && ` +${o.lines.length - 1} more`}</td>
                <td>{o.lines[0] && `${o.lines[0].units_needed} x ${o.lines[0].sales_unit}`}</td>
                <td className="num">{eur(total(o))}</td>
                <td>{heldReason(o)}</td>
                <td>{ago(o.created_at)}</td>
                <td className={`s-${o.status}`}>{LABEL[o.status]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
