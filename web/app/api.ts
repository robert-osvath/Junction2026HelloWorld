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
  status: Status; confirmation_no: string | null; substitutes: Offer[];
};
export type AuditEvent = { id: string; ts: string; actor: string; action: string; hash: string; human_summary: string };
export type AuditVerify = { ok: boolean; checked: number; broken_at: string | null };

export async function api<T>(path: string, method = "GET"): Promise<T> {
  const r = await fetch(API + path, { method, cache: "no-store" });
  if (!r.ok) {
    const body = await r.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `${r.status} ${r.statusText}`);
  }
  return r.json();
}

export const eur = (n: number) => `${n.toFixed(2)} EUR`;
