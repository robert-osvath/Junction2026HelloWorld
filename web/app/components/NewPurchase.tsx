"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { api, helsinkiToday, type CatalogItem, type Need, type Order } from "../api";
import { useNow } from "./hooks";
import Icon from "./Icon";
import Modal from "./Modal";
import { useSavedRules } from "./rules";

type Pick = { id: string; name: string } | null;

// Two steps, then POST /experience/create-draft: the jury picks the best product, so a catalog pick is only a hint.
export default function NewPurchase({ initialQuery, onClose }: { initialQuery: string; onClose: () => void }) {
  const router = useRouter();
  const rules = useSavedRules();
  const now = useNow();
  const today = now ? helsinkiToday(now) : "";
  const [step, setStep] = useState(1);
  const [intent, setIntent] = useState(initialQuery);
  const [pick, setPick] = useState<Pick>(null);
  const [quantity, setQuantity] = useState("");
  const [unit, setUnit] = useState<Need["unit"]>("L");
  const [deliverBy, setDeliverBy] = useState("");
  const [note, setNote] = useState("");
  const [found, setFound] = useState<{ q: string; items: CatalogItem[] }>({ q: "", items: [] });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const q = intent.trim();
  useEffect(() => {
    if (q.length < 2 || pick) return;
    let live = true;
    const t = setTimeout(() => api<CatalogItem[]>(`/catalog/search?q=${encodeURIComponent(q)}&limit=6`)
      .then((items) => live && setFound({ q, items })).catch(() => null), 250);
    return () => { live = false; clearTimeout(t); };
  }, [q, pick]);
  const results = !pick && found.q === q ? found.items : [];

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (step === 1) { setStep(2); return; }
    setBusy(true); setErr("");
    try {
      const order = await api<Order>("/experience/create-draft", "POST", {
        policy_id: rules.policy_id || null,  // none saved: the backend applies the buyer's current/default policy
        buyer_name: rules.buyer || null,
        product_id: pick?.id ?? null,
        product_intent: q,
        quantity: Number(quantity),
        unit,
        deliver_by: deliverBy,
        exception_reason: note.trim() || null,
      });
      onClose();
      router.push(`/approvals/${order.id}`);
    } catch (e2) { setErr((e2 as Error).message); setBusy(false); }
  }

  return (
    <Modal title="Start a new purchase" subtitle="Tell Vimo what the kitchen needs. The jury picks the product and checks your rules." busy={busy} onClose={onClose}>
      <form onSubmit={submit}>
        <div className="stepper" aria-label={`Step ${step} of 2`}>
          <span className="active">1</span><i /><span className={step === 2 ? "active" : ""}>2</span>
          <p>{step === 1 ? "What and how much" : "When and why"}</p>
        </div>
        {step === 1 ? (
          <div className="form-body">
            <label>
              <span>What are you purchasing?</span>
              <input autoFocus required maxLength={160} value={intent} placeholder="e.g. lactose-free milk"
                onChange={(e) => { setIntent(e.target.value); setPick(null); }} />
            </label>
            {pick && <p className="form-note"><Icon name="check" /><span><strong>Catalog hint: {pick.name}</strong>The jury may still choose a better match.</span></p>}
            {results.length > 0 && (
              <ul className="search-results" aria-label="Catalog matches">
                {results.map((r) => (
                  <li key={r.product_id}>
                    <button type="button" onClick={() => { setPick({ id: r.product_id, name: r.name }); setIntent(r.name); }}>
                      <strong>{r.name}</strong>
                      <small>{[r.vendor_name, r.sales_unit, r.claims.slice(0, 2).join(", ")].filter(Boolean).join(" · ")}</small>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className="form-row">
              <label><span>Quantity</span><input required type="number" min="0.01" step="any" value={quantity} onChange={(e) => setQuantity(e.target.value)} /></label>
              <label><span>Unit</span>
                <select value={unit} onChange={(e) => setUnit(e.target.value as Need["unit"])}>
                  <option value="L">litres (L)</option><option value="kg">kilograms (kg)</option><option value="pcs">pieces (pcs)</option>
                </select>
              </label>
            </div>
          </div>
        ) : (
          <div className="form-body">
            <label><span>Deliver by</span><input autoFocus required type="date" min={today || undefined} value={deliverBy} onChange={(e) => setDeliverBy(e.target.value)} /></label>
            <label><span>Note for the approver <em>(optional)</em></span>
              <textarea maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. weekend event, needed even above the usual budget" />
            </label>
            <p className="form-note"><Icon name="sparkles" />
              <span><strong>Ordering for {rules.buyer || "the default buyer"}</strong>
                Orders within your auto-approve limit and rules are placed at once; the rest wait for approval. <Link href="/setup" onClick={onClose}>Edit rules</Link></span>
            </p>
            <p className="muted">{q}: {quantity} {unit}{pick ? ` (hint: ${pick.name})` : ""}</p>
          </div>
        )}
        {err && <p className="err form-err" role="alert">{err}</p>}
        <div className="modal-actions">
          {step === 2 && <button type="button" className="secondary" disabled={busy} onClick={() => setStep(1)}>Back</button>}
          <button type="button" className="secondary" disabled={busy} onClick={onClose}>Cancel</button>
          <button type="submit" className="primary" disabled={busy}>{step === 1 ? "Continue" : busy ? "The jury is checking…" : "Create purchase"}<Icon name="arrow" /></button>
        </div>
      </form>
    </Modal>
  );
}
