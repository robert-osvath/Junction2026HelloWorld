from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

Claim = str  # nutritionalClaim / nonAllergen ids, e.g. "FREE_FROM_LACTOSE", "VEGAN"
Allergen = str  # GS1 codes, e.g. "AM" milk, "AW" gluten


class Mandate(BaseModel):
    id: str
    buyer_name: str
    buyer_type: Literal["hotel", "hospital", "restaurant"]
    budget_per_order_eur: float
    auto_approve_limit_eur: float
    required_claims: list[Claim] = []
    banned_allergens: list[Allergen] = []
    strict_allergens: bool = False
    substitutes_allowed: bool = True


class Need(BaseModel):
    text: str
    product_intent: str
    quantity: float
    unit: Literal["L", "kg", "pcs"]
    deliver_by: date
    extra_constraints: list[str] = []


class Evidence(BaseModel):
    stock: Literal["verified", "estimated", "none"]
    allergens: Literal["declared", "missing"]
    reliability: float | None = None


class Offer(BaseModel):
    product_id: str
    name: str
    sales_unit: str
    units_needed: int
    unit_price_eur: float
    comparison_price_eur: float
    comparison_unit: Literal["KG", "L"]
    total_eur: float
    available_date: date
    closing_time: datetime | None
    sales_block: bool
    logistic_model: Literal["Stock", "Terminal"]
    evidence: Evidence


class Verdict(BaseModel):
    juror: Literal["tier0", "spec", "delivery", "cost", "tier2"]
    decision: Literal["approve", "escalate", "reject"]
    confidence: float = Field(ge=0, le=1)
    reasons: list[str]
    model: str | None = None


class Order(BaseModel):
    id: str
    mandate_id: str
    need: Need
    lines: list[Offer]
    verdicts: list[Verdict] = []
    status: Literal["placed", "pending_approval", "substitutes_proposed", "rejected"]
    confirmation_no: str | None = None
    substitutes: list[Offer] = []
    created_at: datetime | None = None


class AuditEvent(BaseModel):
    id: str
    ts: datetime
    order_id: str | None
    actor: str
    action: str
    payload_hash: str
    prev_hash: str
    hash: str
    human_summary: str


# --- endpoint I/O ---

class CatalogItem(BaseModel):
    product_id: str
    name: str
    vendor_name: str | None
    sales_unit: str
    category: str | None
    claims: list[Claim]
    allergens_contains: list[Allergen]
    allergens_may_contain: list[Allergen]
    allergen_data: bool


class OffersRequest(BaseModel):
    need: Need
    product_ids: list[str] = Field(min_length=1, max_length=1000)
    delivery_date: date


class JuryRequest(BaseModel):
    mandate: Mandate
    need: Need
    offers: list[Offer]


class JuryResult(BaseModel):
    verdicts: list[Verdict]
    decision: Literal["auto_order", "human_approval", "substitutes", "reject"]
    chosen_product_id: str | None


class SubstitutesRequest(BaseModel):
    mandate: Mandate
    need: Need
    offer: Offer


class OrderRequest(BaseModel):
    mandate_id: str
    need: Need
    lines: list[Offer]


class RejectRequest(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class AuditVerify(BaseModel):
    ok: bool
    checked: int
    broken_at: str | None = None
