"use client";
import Link from "next/link";
import { useState } from "react";
import { api, eur, heldReason, total, type Order } from "../api";
import Icon from "./Icon";
import Modal from "./Modal";

// Approve or reject one held order. Reject needs a reason (the API requires it for the audit log).
export default function ApprovalDialog({ order, onClose, onDone }: { order: Order; onClose: () => void; onDone: (message: string) => void }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const line = order.lines[0];
  const approvals = order.verdicts.filter((v) => v.decision === "approve").length;
  const clean = approvals === order.verdicts.length;

  async function act(path: "approve" | "reject") {
    setBusy(true); setErr("");
    try {
      const next = await api<Order>(`/orders/${order.id}/${path}`, "POST", path === "reject" ? { reason: reason.trim() } : undefined);
      onDone(path === "approve"
        ? `Approved, order placed${next.confirmation_no ? ` (${next.confirmation_no})` : ""}`
        : "Order rejected, reason saved to the audit log");
    } catch (e) { setErr((e as Error).message); setBusy(false); }
  }

  return (
    <Modal title="Review approval" subtitle={`${order.need.text} · ${order.mandate_id}`} onClose={onClose}>
      <div className="approval-dialog-summary">
        <div><span>Order total</span><strong>{eur(total(order))}</strong><small>{line ? `${line.units_needed} x ${line.sales_unit}, delivery ${line.available_date}` : "No order lines"}</small></div>
        <div><span>Product</span><strong>{line?.name ?? "—"}</strong><small>Jury: {approvals} of {order.verdicts.length} approve</small></div>
      </div>
      <div className={`recommendation ${clean ? "" : "held"}`}>
        <Icon name={clean ? "check" : "shield"} />
        <div>
          <strong>{clean ? "Every juror approved" : "Held for your decision"}</strong>
          <p>{clean ? "All policy, delivery and cost checks passed." : heldReason(order)}</p>
        </div>
      </div>
      <div className="form-body compact">
        <label>
          <span>Reason <em>(required to reject)</em></span>
          <textarea value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} placeholder="Why are you rejecting this order?" />
        </label>
        {err && <p className="err" role="alert">{err}</p>}
        <Link className="text-button" href={`/approvals/${order.id}`}>Open full record <Icon name="arrow" size={15} /></Link>
      </div>
      <div className="modal-actions split">
        <button className="danger-button" disabled={busy || !reason.trim()} onClick={() => void act("reject")}>Reject</button>
        <div>
          <button className="secondary" disabled={busy} onClick={onClose}>Cancel</button>
          <button className="primary" disabled={busy} onClick={() => void act("approve")}><Icon name="check" />Approve order</button>
        </div>
      </div>
    </Modal>
  );
}
