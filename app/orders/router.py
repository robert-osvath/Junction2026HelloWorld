"""P4. Orders (T12) and audit (T13)."""
import math
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.catalog.aimo import AimoError
from app.catalog.offers import InvalidOfferRequest
from app.orders import flow, policies, service
from app.orders.audit import get_store
from app.orders.store import get_order_store
from contracts import AcceptSubstituteRequest, AuditEvent, AuditVerify, Need, Order, OrderRequest, RejectRequest

router = APIRouter(tags=["orders"])


@router.post("/orders")
def create_order(req: OrderRequest) -> Order:
    return service.create(req)


class FromNeedRequest(BaseModel):
    mandate_id: str | None = None  # default: the Vimo default policy
    need: Need
    product_hint: str | None = None
    simulate_out_of_stock: bool = False  # explicit demo switch


@router.post("/orders/from-need")
def create_order_from_need(req: FromNeedRequest) -> Order:
    mandate = policies.resolve(req.mandate_id) if req.mandate_id else policies.current()
    if mandate is None:
        raise HTTPException(404, f"unknown mandate {req.mandate_id!r}")
    if not (math.isfinite(req.need.quantity) and req.need.quantity > 0):
        raise HTTPException(422, "quantity must be a positive finite number")
    try:
        return flow.order_from_need(mandate, req.need, product_hint=req.product_hint,
                                    simulate_out_of_stock=req.simulate_out_of_stock)
    except AimoError as e:
        raise HTTPException(502, "Valio Aimo API unavailable") from e
    except InvalidOfferRequest as e:
        raise HTTPException(422, str(e)) from e


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


@router.post("/orders/{order_id}/accept-substitute")
def accept_substitute(order_id: str, body: AcceptSubstituteRequest) -> Order:
    return service.accept_substitute(order_id, body.product_id)


@router.get("/audit")
def audit(order_id: str | None = None, orderId: str | None = None) -> list[AuditEvent]:  # noqa: N803 (Notion spelling)
    return get_store().events(order_id if order_id is not None else orderId)


@router.get("/audit/verify")
def verify() -> AuditVerify:
    return get_store().verify()
