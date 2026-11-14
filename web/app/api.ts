export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Evidence = { stock: "verified" | "estimated" | "none"; allergens: "declared" | "missing"; reliability: number | null };
export type Offer = {
  product_id: string; name: string; sales_unit: string; units_needed: number; unit_price_eur: number;
  total_eur: number; available_date: string; closing_time: string | null; sales_block: boolean;
  logistic_model: string; evidence: Evidence;
};
export type Verdict = { juror: string; decision: "approve" | "escalate" | "reject"; confidence: number; reasons: string[]; model: string | null };
export type Status = "pending_approval" | "placed" | "substitutes_proposed" | "rejected";
export type Order = {
  id: string; mandate_id: string; need: { text: string }; lines: Offer[]; verdicts: Verdict[];
  status: Status; confirmation_no: string | null; substitutes: Offer[]; created_at: string | null;
};
export type RejectRequest = { reason: string };
export type AuditEvent = { id: string; ts: string; actor: string; action: string; hash: string; human_summary: string };
export type AuditVerify = { ok: boolean; checked: number; broken_at: string | null };

export async function api<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const r = await fetch(API + path, {
    method, cache: "no-store",
    ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `${r.status} ${r.statusText}`);
  }
  return r.json();
}

export const eur = (n: number) => new Intl.NumberFormat("en-GB", { style: "currency", currency: "EUR" }).format(n);
export const dt = (s: string) => new Date(s).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
export const LABEL: Record<Status, string> = {
  pending_approval: "Pending approval", placed: "Placed", substitutes_proposed: "Substitutes proposed", rejected: "Rejected",
};
export const total = (o: Order) => o.lines.reduce((s, l) => s + l.total_eur, 0);
export const heldReason = (o: Order) => o.verdicts.find((v) => v.decision !== "approve")?.reasons[0] ?? "—";
export function ago(s: string | null, now = Date.now()) {
  if (!s) return "—";
  const m = Math.max(0, Math.round((now - new Date(s).getTime()) / 60000));
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.floor(m / 60)} h ago` : `${Math.floor(m / 1440)} d ago`;
}
