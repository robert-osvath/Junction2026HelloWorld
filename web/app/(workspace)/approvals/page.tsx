"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ago, day, eur, heldReason, helsinkiToday, LABEL, total, type AuditVerify, type Order, type Status } from "../../api";
import ApprovalDialog from "../../components/ApprovalDialog";
import { useNow, usePoll, useToast } from "../../components/hooks";
import Icon from "../../components/Icon";
import Toast from "../../components/Toast";

const TABS = Object.keys(LABEL) as Status[];

export default function Page() {
  return <Suspense fallback={<p className="empty-state">Loading…</p>}><Inbox /></Suspense>;
}

function Inbox() {
  const router = useRouter();
  const param = useSearchParams().get("status") as Status | null;  // deep links: bell, overview cards, the Vimo cockpit
  // the URL is the tab state, so the bell link switches tabs even when the inbox is already open
  const tab: Status = param && Object.hasOwn(LABEL, param) ? param : "pending_approval";
  const setTab = (t: Status) => router.replace(`/approvals?status=${t}`, { scroll: false });
  const now = useNow();
  const orders = usePoll<Order[]>("/orders");
  const verify = usePoll<AuditVerify>("/audit/verify", 30_000);
  const [open, setOpen] = useState<Order | null>(null);
  const [toast, setToast] = useToast();

  const all = orders.data ?? [];
  const today = now ? helsinkiToday(now) : "";
  const of = (s: Status) => all.filter((o) => o.status === s);
  const rows = of(tab).sort((a, b) => a.need.deliver_by.localeCompare(b.need.deliver_by));
  const sum = (os: Order[]) => eur(os.reduce((s, o) => s + total(o), 0));
  const pick = (o: Order) => (o.status === "pending_approval" ? setOpen(o) : router.push(`/approvals/${o.id}`));

  return (
    <>
      <section className="page-heading">
        <div>
          <p className="eyebrow">{of("pending_approval").length} waiting for you</p>
          <h1>Approval inbox</h1>
          <p>Review what the jury held, approve or reject, and check the audit trail.</p>
        </div>
      </section>
      {orders.err && <p className="err" role="alert">Backend: {orders.err}</p>}

      <div className="approval-layout">
        <div className="approval-list">
          <div className="table-tools">
            <nav className="tabs" aria-label="Order status">
              {TABS.map((t) => (
                <button key={t} aria-current={t === tab} onClick={() => setTab(t)}>{LABEL[t]} <span>{of(t).length}</span></button>
              ))}
            </nav>
          </div>
          {orders.data === null ? <p className="empty-state">Loading…</p>
            : rows.length === 0 ? <p className="empty-state"><Icon name="check" />No orders with status {LABEL[tab].toLowerCase()}.</p>
            : rows.map((o) => {
              const due = today !== "" && o.need.deliver_by <= today && (o.status === "pending_approval" || o.status === "substitutes_proposed");
              return (
                <button className="approval-row" key={o.id} onClick={() => pick(o)}
                  aria-label={`${o.need.text}, ${eur(total(o))}, ${LABEL[o.status]}${o.status === "pending_approval" ? ", review" : ", open record"}`}>
                  <span className="approval-icon" aria-hidden="true"><Icon name={o.status === "pending_approval" ? "briefcase" : "file"} /></span>
                  <span className="approval-copy">
                    <span><span className={`status s-${o.status}`}>{LABEL[o.status]}</span>{due && <span className="tag urgent">Due today</span>}</span>
                    <strong>{o.need.text}</strong>
                    <small>{o.mandate_id} · {o.lines[0]?.name ?? "no product"} · {o.status === "placed" ? (o.confirmation_no ?? "placed") : heldReason(o)}</small>
                  </span>
                  <span className="approval-value">
                    <strong>{eur(total(o))}</strong>
                    <span className={due ? "due" : ""}>Deliver by {day(o.need.deliver_by)}</span>
                    <span>{ago(o.created_at, now || undefined)}</span>
                  </span>
                  <Icon name="chevron" />
                </button>
              );
            })}
        </div>

        <aside className="approval-summary">
          <p className="eyebrow">All orders</p>
          <h2>Approval stats</h2>
          <div className="approval-stat"><strong>{of("pending_approval").length}</strong><span>Waiting, {sum(of("pending_approval"))}</span></div>
          <div className="approval-stat"><strong>{of("placed").length}</strong><span>Placed, {sum(of("placed"))}</span></div>
          <div className="approval-stat"><strong>{of("rejected").length}</strong><span>Rejected</span></div>
          <div className={`healthy ${verify.data && !verify.data.ok ? "bad" : ""}`}>
            <span><Icon name={verify.data?.ok === false ? "close" : "check"} /></span>
            <p>{verify.data === null ? <strong>Checking audit log…</strong>
              : verify.data.ok ? <><strong>Audit log verified</strong>{verify.data.checked} entries, none changed.</>
              : <><strong>Audit log broken</strong>at entry {verify.data.broken_at}.</>}</p>
          </div>
        </aside>
      </div>

      {open && <ApprovalDialog order={open} onClose={() => setOpen(null)} onDone={(m) => { setOpen(null); setToast(m); void orders.reload(); }} />}
      <Toast message={toast} />
    </>
  );
}
