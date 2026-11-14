"use client";
import Link from "next/link";
import { useState } from "react";
import { day, dueLabel, eur, helsinkiToday, LABEL, shortId, total, WAITING, who, type AuditEvent, type AuditVerify, type Order } from "../api";
import ApprovalDialog from "../components/ApprovalDialog";
import { useNow, usePoll, useToast } from "../components/hooks";
import Icon from "../components/Icon";
import { useSavedRules } from "../components/Shell";
import Toast from "../components/Toast";

const initials = (s: string) => s.replace(/^Juror: /, "").split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase() ?? "").join("");

function greeting(now: number) {
  const h = Number(new Date(now).toLocaleString("en-GB", { hour: "numeric", hourCycle: "h23", timeZone: "Europe/Helsinki" }));
  return h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export default function Overview() {
  const now = useNow();
  const { buyer } = useSavedRules();
  const orders = usePoll<Order[]>("/orders");
  const audit = usePoll<AuditEvent[]>("/audit", 15_000);
  const verify = usePoll<AuditVerify>("/audit/verify", 30_000);
  const [open, setOpen] = useState<Order | null>(null);
  const [toast, setToast] = useToast();

  const all = orders.data ?? [];
  const today = now ? helsinkiToday(now) : "";
  const of = (s: Order["status"]) => all.filter((o) => o.status === s);
  const pending = of("pending_approval");
  const subs = of("substitutes_proposed");
  const queue = all.filter((o) => WAITING.includes(o.status)).sort((a, b) => a.need.deliver_by.localeCompare(b.need.deliver_by));
  const recent = [...(audit.data ?? [])].sort((a, b) => b.ts.localeCompare(a.ts)).slice(0, 4);
  const err = orders.err || audit.err;

  return (
    <>
      <section className="hero-row">
        <div>
          <p className="eyebrow">{now ? new Date(now).toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", timeZone: "Europe/Helsinki" }) : " "}</p>
          <h1>{now ? greeting(now) : "Welcome"}{buyer ? `, ${buyer}` : ""}.</h1>
          <p>Here’s what needs your attention across purchasing today.</p>
        </div>
        <a className="primary" href="/vimo/index.html"><Icon name="sparkles" />Ask Vimo for an order</a>
      </section>
      {err && <p className="err" role="alert">Backend: {err}</p>}

      <div className="summary-grid">
        <Link className="summary-card lime-card" href="/approvals?status=pending_approval">
          <span className="summary-icon"><Icon name="check" /></span>
          <span><span className="label">Waiting for you</span><strong>{pending.length} {pending.length === 1 ? "approval" : "approvals"}</strong>
            <small>{pending.filter((o) => dueLabel(o.need.deliver_by, today)).length} due today or overdue</small></span>
          <span className="summary-go" aria-hidden="true"><Icon name="arrow" /></span>
        </Link>
        <Link className="summary-card peach-card" href="/approvals?status=substitutes_proposed">
          <span className="summary-icon"><Icon name="users" /></span>
          <span><span className="label">Jury decisions</span><strong>{subs.length} substitute {subs.length === 1 ? "choice" : "choices"}</strong>
            <small>Pick a substitute or reject</small></span>
          <span className="summary-go" aria-hidden="true"><Icon name="arrow" /></span>
        </Link>
        <Link className="summary-card cream-card" href="/approvals?status=placed">
          <span className="summary-icon"><Icon name="briefcase" /></span>
          <span><span className="label">Placed orders</span><strong>{eur(of("placed").reduce((s, o) => s + total(o), 0))}</strong>
            <small>{of("placed").length} placed, {pending.length + subs.length} waiting</small></span>
          <span className="summary-go" aria-hidden="true"><Icon name="arrow" /></span>
        </Link>
      </div>

      <div className="dashboard-grid">
        <article className="panel priorities">
          <div className="panel-heading">
            <div><p className="eyebrow">Your queue</p><h2>Today’s priorities</h2></div>
            <Link className="text-button" href="/approvals">View all <Icon name="arrow" size={15} /></Link>
          </div>
          {orders.loading ? <p className="empty-state">Loading…</p>
            : queue.length === 0 ? <p className="empty-state"><Icon name="check" />Nothing waiting. The jury handled every order within your rules.</p>
            : queue.slice(0, 5).map((o) => {
              const due = dueLabel(o.need.deliver_by, today);
              const [d, m] = day(o.need.deliver_by).split(" ");
              return (
                <div className="priority-item" key={o.id}>
                  <div className={`date-box ${due ? "" : "soft"}`}><span className="sr-only">Deliver by </span><strong>{d}</strong><span>{m}</span></div>
                  <div className="priority-copy">
                    <div>{due && <span className="tag urgent">{due}</span>}<span className={`tag ${o.status === "substitutes_proposed" ? "jury-tag" : ""}`}>{LABEL[o.status]}</span></div>
                    <h3>{o.need.text}</h3>
                    <p>{o.lines[0]?.name ?? "No product"} · {eur(total(o))} · {o.mandate_id}</p>
                  </div>
                  {o.status === "pending_approval"
                    ? <button className="secondary" onClick={() => setOpen(o)}>Review</button>
                    : <Link className="secondary" href={`/approvals/${o.id}`}>Choose</Link>}
                </div>
              );
            })}
        </article>

        <article className="panel activity">
          <div className="panel-heading"><div><p className="eyebrow">Live updates</p><h2>Recent activity</h2></div></div>
          {recent.length === 0 ? <p className="empty-state">No activity yet.</p> : recent.map((e) => (
            <div className="activity-item" key={e.id}>
              <span className="avatar" aria-hidden="true">{initials(who(e.actor))}</span>
              <div>
                <strong>{who(e.actor)}{e.order_id ? ` · ${shortId(e.order_id)}` : ""}</strong>
                <p>{e.human_summary}</p>
              </div>
            </div>
          ))}
          <Link className="activity-link" href="/timeline">Open full audit timeline <Icon name="arrow" size={15} /></Link>
        </article>
      </div>

      <article className={`insight-banner ${verify.data && !verify.data.ok ? "bad" : ""}`}>
        <span className="insight-icon"><Icon name="shield" size={23} /></span>
        <div>
          <p className="eyebrow">Audit log</p>
          <h3>{verify.err ? "Audit check unavailable" : verify.data === null ? "Checking the audit chain…" : verify.data.ok ? `Verified: ${verify.data.checked} entries, none changed` : `Broken at entry ${verify.data.broken_at}`}</h3>
          <p>Every request, juror verdict and human decision is hash-chained, so any edit shows up here.</p>
        </div>
        <Link href="/timeline">See the timeline <Icon name="arrow" size={15} /></Link>
      </article>

      {open && <ApprovalDialog order={open} onClose={() => setOpen(null)} onDone={(m) => { setOpen(null); setToast(m); orders.reload(); audit.reload(); verify.reload(); }} />}
      <Toast message={toast} />
    </>
  );
}
