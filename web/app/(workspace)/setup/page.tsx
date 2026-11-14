"use client";
import Link from "next/link";
import { useState, type FormEvent } from "react";
import { api, type AuditEvent, type Mandate, type Order } from "../../api";
import { usePoll, useToast } from "../../components/hooks";
import Icon from "../../components/Icon";
import Modal from "../../components/Modal";
import { saveRules, useSavedRules, type SavedRules } from "../../components/rules";
import Toast from "../../components/Toast";

const csv = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
const claim = (c: string) => { const v = c.toUpperCase().replace(/[ -]+/g, "_"); return v === "LACTOSE_FREE" ? "FREE_FROM_LACTOSE" : v; };

// Same fields and storage as the Vimo cockpit's rules dialog, so both stay in sync.
function RulesDialog({ current, saved, onClose, onSaved }: { current: Mandate | null; saved: SavedRules; onClose: () => void; onSaved: (m: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.currentTarget)) as Record<string, string>;
    setBusy(true); setErr("");
    try {
      const res = await api<{ policy_id: string; summary: string }>("/experience/policies", "POST", {
        buyer_name: f.buyer, buyer_type: f.buyer_type, auto_approve_limit: Number(f.limit), budget: Number(f.budget),
        required_claims: csv(f.claim ?? "").map(claim), banned_allergens: csv(f.allergens ?? "").map((c) => c.toUpperCase()),
        strict_allergens: Boolean(f.strict_allergens), substitutes_allowed: Boolean(f.substitutes),
      });
      saveRules({ ...f, policy_id: res.policy_id });
      onSaved(res.summary);
    } catch (e2) { setErr((e2 as Error).message); setBusy(false); }
  }
  const v = (k: keyof SavedRules, fallback: string) => saved[k] ?? fallback;
  return (
    <Modal title="Your buyer rules" subtitle="The jury checks every order against these. Vimo uses the same rules." busy={busy} onClose={onClose}>
      <form onSubmit={submit}>
        <div className="form-body">
          <div className="form-row">
            <label><span>Buyer / location</span><input name="buyer" required maxLength={120} defaultValue={v("buyer", current?.buyer_name ?? "My kitchen")} /></label>
            <label><span>Buyer type</span>
              <select name="buyer_type" defaultValue={v("buyer_type", current?.buyer_type ?? "restaurant")}>
                <option value="restaurant">Restaurant</option><option value="hotel">Hotel</option><option value="hospital">Hospital</option>
              </select>
            </label>
          </div>
          <div className="form-row">
            <label><span>Auto-approve limit (€)</span><input name="limit" type="number" min="0" step="any" required defaultValue={v("limit", String(current?.auto_approve_limit_eur ?? 500))} /></label>
            <label><span>Order budget (€)</span><input name="budget" type="number" min="1" step="any" required defaultValue={v("budget", String(current?.budget_per_order_eur ?? 2000))} /></label>
          </div>
          <label><span>Required claims <em>(optional, comma separated)</em></span><input name="claim" placeholder="e.g. lactose-free, vegan" defaultValue={v("claim", current?.required_claims.join(", ") ?? "")} /></label>
          <label><span>Blocked allergens <em>(GS1 codes, e.g. AM milk, AW gluten)</em></span>
            <input name="allergens" placeholder="e.g. AM, AW" pattern="\s*[A-Za-z]{2}(\s*,\s*[A-Za-z]{2})*\s*" title="Two-letter GS1 codes separated by commas, e.g. AM, AW"
              defaultValue={v("allergens", current?.banned_allergens.join(", ") ?? "")} />
          </label>
          <label className="check"><input name="substitutes" type="checkbox" defaultChecked={saved.policy_id ? saved.substitutes === "on" : current?.substitutes_allowed ?? true} /> Allow substitutes that keep the dietary and allergen rules</label>
          <label className="check"><input name="strict_allergens" type="checkbox" defaultChecked={saved.policy_id ? saved.strict_allergens === "on" : current?.strict_allergens ?? false} /> Strict: also reject products that may contain a blocked allergen</label>
        </div>
        {err && <p className="err form-err" role="alert">{err}</p>}
        <div className="modal-actions">
          <button type="button" className="secondary" disabled={busy} onClick={onClose}>Cancel</button>
          <button type="submit" className="primary" disabled={busy}><Icon name="check" />Save rules</button>
        </div>
      </form>
    </Modal>
  );
}

export default function Setup() {
  const saved = useSavedRules();
  const policy = usePoll<Mandate>(`/experience/policy${saved.buyer ? `?buyer_name=${encodeURIComponent(saved.buyer)}` : ""}`, 60_000);
  const orders = usePoll<Order[]>("/orders", 15_000);
  const audit = usePoll<AuditEvent[]>("/audit", 15_000);
  const [editing, setEditing] = useState(false);
  const [toast, setToast] = useToast();

  const all = orders.data ?? [];
  const steps = [
    { title: "Set your buyer rules", text: "Auto-approve limit, budget, required claims and blocked allergens.", done: Boolean(saved.policy_id),
      action: <button className={saved.policy_id ? "text-button" : "secondary"} onClick={() => setEditing(true)}>{saved.policy_id ? "Edit" : "Set up"}{!saved.policy_id && <Icon name="arrow" size={15} />}</button> },
    { title: "Ask for your first order", text: "By voice or text with Vimo, or with New purchase in the top bar.", done: all.length > 0,
      action: <a className="secondary" href="/vimo/index.html">Ask Vimo <Icon name="arrow" size={15} /></a> },
    { title: "See the jury decide", text: "Code gates and model jurors check spec, delivery, cost and sustainability.", done: all.some((o) => o.verdicts.length > 0),
      action: <Link className="secondary" href="/timeline">Open timeline <Icon name="arrow" size={15} /></Link> },
    { title: "Approve a held order", text: "Orders over your limit or off-policy wait for you in the approval inbox.", done: (audit.data ?? []).some((e) => e.actor === "human"),
      action: <Link className="secondary" href="/approvals">Open inbox <Icon name="arrow" size={15} /></Link> },
  ];
  const done = steps.filter((s) => s.done).length;
  const p = policy.data;

  return (
    <div className="page-narrow">
      <div className="onboarding-hero">
        <div className="orb"><Icon name="sparkles" size={34} /></div>
        <p className="eyebrow">Welcome to Vimo</p>
        <h1>Set your kitchen up for trusted ordering</h1>
        <p>Four steps from rules to your first approved order.</p>
        <div className="setup-progress">
          <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={4} aria-valuenow={done} aria-label="Setup progress"><span style={{ width: `${done * 25}%` }} /></div>
          <strong>{done} of 4 complete</strong>
        </div>
      </div>
      {(orders.err || policy.err) && <p className="err" role="alert">Backend: {orders.err || policy.err}</p>}
      <ol className="step-list">
        {steps.map((s, i) => (
          <li className={s.done ? "done" : ""} key={s.title}>
            <span className="step-number">{s.done ? <><Icon name="check" /><span className="sr-only">Done: </span></> : i + 1}</span>
            <div><h3>{s.title}</h3><p>{s.text}</p></div>
            {s.action}
          </li>
        ))}
      </ol>
      {p && (
        <div className="guide-card">
          <span><Icon name="shield" /></span>
          <section>
            <strong>Rules in force for {p.buyer_name}{saved.policy_id ? "" : " (default)"}</strong>
            <p>Auto-approve up to €{p.auto_approve_limit_eur.toLocaleString("en-GB")}, budget €{p.budget_per_order_eur.toLocaleString("en-GB")} per order
              {p.required_claims.length ? `, requires ${p.required_claims.join(", ")}` : ""}{p.banned_allergens.length ? `, blocks ${p.banned_allergens.join(", ")}` : ""}
              {p.substitutes_allowed ? ", substitutes allowed" : ", no substitutes"}.</p>
          </section>
          <button className="text-button" onClick={() => setEditing(true)}>Edit <Icon name="arrow" size={15} /></button>
        </div>
      )}
      {editing && <RulesDialog current={p} saved={saved} onClose={() => setEditing(false)} onSaved={(m) => { setEditing(false); setToast(m); policy.reload(); }} />}
      <Toast message={toast} />
    </div>
  );
}
