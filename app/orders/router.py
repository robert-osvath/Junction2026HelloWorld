"""P4 (T12-T13). Mock: returns fixtures; approve/reject only flip status, nothing is stored."""
from fastapi import APIRouter

from app.mocks import load
from contracts import AuditEvent, AuditVerify, Order, OrderRequest

router = APIRouter(tags=["orders"])


@router.post("/orders")
def create_order(req: OrderRequest) -> Order:
    return load("order")


@router.get("/orders/{order_id}")
def get_order(order_id: str) -> Order:
    return load("order").model_copy(update={"id": order_id})


@router.post("/orders/{order_id}/approve")
def approve(order_id: str) -> Order:
    return load("order").model_copy(update={"id": order_id, "status": "placed"})


@router.post("/orders/{order_id}/reject")
def reject(order_id: str) -> Order:
    return load("order").model_copy(update={"id": order_id, "status": "rejected", "confirmation_no": None})


@router.get("/audit")
def audit(order_id: str | None = None) -> list[AuditEvent]:
    return [load("audit_event")]


@router.get("/audit/verify")
def verify() -> AuditVerify:
    return AuditVerify(ok=True, checked=1)
