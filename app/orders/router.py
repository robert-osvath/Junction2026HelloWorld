"""P4. Orders (T12) and audit (T13)."""
from typing import Literal

from fastapi import APIRouter

from app.orders import service
from app.orders.audit import get_store
from app.orders.store import get_order_store
from contracts import AuditEvent, AuditVerify, Order, OrderRequest, RejectRequest

router = APIRouter(tags=["orders"])


@router.post("/orders")
def create_order(req: OrderRequest) -> Order:
    return service.create(req)


@router.get("/orders")
def list_orders(status: Literal["placed", "pending_approval", "substitutes_proposed", "rejected"] | None = None) -> list[Order]:
    return get_order_store().list(status)


@router.get("/orders/{order_id}")
def get_order(order_id: str) -> Order:
    return service.get(order_id)


@router.post("/orders/{order_id}/approve")
def approve(order_id: str) -> Order:
    return service.approve(order_id)


@router.post("/orders/{order_id}/reject")
def reject(order_id: str, body: RejectRequest | None = None) -> Order:
    return service.reject(order_id, body.reason if body else None)


@router.get("/audit")
def audit(order_id: str | None = None, orderId: str | None = None) -> list[AuditEvent]:  # noqa: N803 (Notion spelling)
    return get_store().events(order_id if order_id is not None else orderId)


@router.get("/audit/verify")
def verify() -> AuditVerify:
    return get_store().verify()
