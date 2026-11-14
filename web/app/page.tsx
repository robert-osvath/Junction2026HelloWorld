"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, eur, type Order, type Status } from "./api";

const TABS: Status[] = ["pending_approval", "placed", "substitutes_proposed", "rejected"];

export default function Inbox() {
  const [tab, setTab] = useState<Status>("pending_approval");
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    let live = true;
    api<Order[]>(`/orders?status=${tab}`)
      .then((o) => live && (setOrders(o), setErr("")))
      .catch((e) => live && (setOrders([]), setErr(e.message)));
    return () => { live = false; };
  }, [tab]);

  return (
    <>
      <nav className="tabs">
        {TABS.map((t) => (
          <button key={t} className={t === tab ? "on" : ""} onClick={() => { setOrders(null); setTab(t); }}>
            {t.replace("_", " ")}
          </button>
        ))}
      </nav>
      {err && <p className="err">{err}</p>}
      {orders === null ? <p>Loading...</p> : orders.length === 0 ? <p>No {tab.replace("_", " ")} orders.</p> : (
        <table>
          <thead><tr><th>Mandate</th><th>Product</th><th>Units</th><th>Total</th><th>Status</th></tr></thead>
          <tbody>
            {orders.map((o) => (
              <tr key={o.id}>
                <td><Link href={`/orders/${o.id}`}>{o.mandate_id}</Link></td>
                <td>{o.lines.map((l) => l.name).join(", ")}</td>
                <td>{o.lines.map((l) => l.units_needed).join(", ")}</td>
                <td>{eur(o.lines.reduce((s, l) => s + l.total_eur, 0))}</td>
                <td><span className={`badge ${o.status}`}>{o.status}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
