"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { day, eur, LABEL, total, type Order, type Status } from "../../api";
import { usePoll } from "../../components/hooks";
import Icon from "../../components/Icon";
import NewPurchase from "../../components/NewPurchase";

type Tab = Status | "all";
const TABS: Tab[] = ["all", "pending_approval", "placed", "substitutes_proposed", "rejected"];

export default function Page() {
  return <Suspense fallback={<p className="empty-state">Loading…</p>}><Sourcing /></Suspense>;
}

function Sourcing() {
  const router = useRouter();
  const param = useSearchParams().get("status") as Tab | null;  // deep link from the overview cards
  const orders = usePoll<Order[]>("/orders");
  const [tab, setTab] = useState<Tab>(param && TABS.includes(param) ? param : "all");
  const [filter, setFilter] = useState("");
  const [buying, setBuying] = useState(false);

  const all = orders.data ?? [];
  const f = filter.trim().toLowerCase();
  const rows = all
    .filter((o) => tab === "all" || o.status === tab)
    .filter((o) => !f || `${o.need.text} ${o.lines[0]?.name ?? ""} ${o.mandate_id}`.toLowerCase().includes(f))
    .sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
  const placed = all.filter((o) => o.status === "placed");
  // placed with every juror approving = no human was needed
  const auto = placed.filter((o) => o.verdicts.length > 0 && o.verdicts.every((v) => v.decision === "approve"));
  const count = (t: Tab) => (t === "all" ? all.length : all.filter((o) => o.status === t).length);

  return (
    <>
      <section className="page-heading">
        <div>
          <p className="eyebrow">Ordering pipeline</p>
          <h1>Sourcing</h1>
          <p>Every order a buyer agent or person started, with the product the jury picked.</p>
        </div>
        <button className="primary" onClick={() => setBuying(true)}><Icon name="plus" />New purchase</button>
      </section>
      {orders.err && <p className="err" role="alert">Backend: {orders.err}</p>}

      <div className="mini-stats">
        <div><span>Orders</span><strong>{all.length}</strong></div>
        <div><span>Placed value</span><strong>{eur(placed.reduce((s, o) => s + total(o), 0))}</strong></div>
        <div><span>Placed without a person</span><strong>{placed.length ? `${Math.round((auto.length / placed.length) * 100)}%` : "—"}</strong></div>
        <div><span>Jury verdicts</span><strong>{all.reduce((s, o) => s + o.verdicts.length, 0)}</strong></div>
      </div>

      <div className="table-panel">
        <div className="table-tools">
          <nav className="tabs" aria-label="Order status">
            {TABS.map((t) => (
              <button key={t} aria-current={t === tab} onClick={() => setTab(t)}>{t === "all" ? "All orders" : LABEL[t]} <span>{count(t)}</span></button>
            ))}
          </nav>
          <label className="search-box filter">
            <Icon name="filter" size={16} />
            <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter orders…" aria-label="Filter orders by request, product or buyer" />
          </label>
        </div>
        {orders.loading ? <p className="empty-state">Loading…</p> : orders.data === null ? <p className="empty-state">Orders could not be loaded.</p> : rows.length === 0 ? <p className="empty-state">No matching orders.</p> : (
          <div className="table-wrap"><table>
            <thead>
              <tr><th scope="col">Request</th><th scope="col">Status</th><th scope="col" className="num">Total</th><th scope="col">Deliver by</th><th scope="col">Buyer</th><th scope="col"><span className="sr-only">Open</span></th></tr>
            </thead>
            <tbody>
              {rows.map((o) => (
                <tr key={o.id} className="click" onClick={() => router.push(`/approvals/${o.id}`)}>
                  <td>
                    <div className="project-name">
                      <span className="project-icon" aria-hidden="true"><Icon name="briefcase" /></span>
                      <div><Link href={`/approvals/${o.id}`} onClick={(e) => e.stopPropagation()}><strong>{o.need.text}</strong></Link>
                        <small>{o.lines[0] ? `${o.lines[0].units_needed} x ${o.lines[0].sales_unit} ${o.lines[0].name}` : "no product"}</small></div>
                    </div>
                  </td>
                  <td><span className={`status s-${o.status}`}>{LABEL[o.status]}</span></td>
                  <td className="num"><strong>{eur(total(o))}</strong></td>
                  <td>{day(o.need.deliver_by)}</td>
                  <td>{o.mandate_id}</td>
                  <td aria-hidden="true"><Icon name="chevron" /></td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </div>
      {buying && <NewPurchase initialQuery="" onClose={() => setBuying(false)} />}
    </>
  );
}
