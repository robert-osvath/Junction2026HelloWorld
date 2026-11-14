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
    juror: Literal["tier0", "spec", "delivery", "cost", "sustainability", "tier2"]
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
    country_of_origin: str | None = None
    labels: list[str] = []
    temperature_condition: int | None = None
    co2_kg_per_kg: float | None = None


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


class AcceptSubstituteRequest(BaseModel):
    product_id: str


class AuditVerify(BaseModel):
    ok: bool
    checked: int
    broken_at: str | None = None


# --- kitchen planning ---

class RecipeIngredient(BaseModel):
    product_intent: str = Field(min_length=1, max_length=160)
    quantity_per_serving: float = Field(gt=0)
    unit: Literal["L", "kg", "pcs"]
    required_claims: list[Claim] = []
    notes: str | None = Field(default=None, max_length=300)


class PlannedRecipe(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    servings: int = Field(gt=0, le=100000)
    service_date: date
    ingredients: list[RecipeIngredient] = Field(min_length=1, max_length=100)


class InventoryLevel(BaseModel):
    product_intent: str = Field(min_length=1, max_length=160)
    quantity: float = Field(ge=0)
    unit: Literal["L", "kg", "pcs"]
    usable_until: date | None = None


class RecipeGapRequest(BaseModel):
    recipes: list[PlannedRecipe] = Field(min_length=1, max_length=100)
    inventory: list[InventoryLevel] = Field(default_factory=list, max_length=1000)
    today: date


class RecipeGap(BaseModel):
    product_intent: str
    quantity_needed: float
    quantity_on_hand: float
    quantity_to_order: float
    unit: Literal["L", "kg", "pcs"]
    required_by: date
    required_claims: list[Claim] = []
    recipe_names: list[str]
    urgency: Literal["normal", "urgent"]
    explanation: str


class RecipeGapResponse(BaseModel):
    gaps: list[RecipeGap]
    usable_inventory_value: int
    expired_or_unusable_inventory_value: int


class StandingOrderObservation(BaseModel):
    date: date
    quantity: float = Field(ge=0)


class StandingOrderDriftRequest(BaseModel):
    product_intent: str = Field(min_length=1, max_length=160)
    proposed_quantity: float = Field(ge=0)
    unit: Literal["L", "kg", "pcs"]
    history: list[StandingOrderObservation] = Field(min_length=3, max_length=104)
    tolerance_pct: float = Field(default=0.2, ge=0.01, le=1.0)


class StandingOrderDriftResponse(BaseModel):
    product_intent: str
    baseline_quantity: float
    proposed_quantity: float
    delta_pct: float
    status: Literal["within_pattern", "review_required"]
    explanation: str


# --- last-minute event plan ---

class EventPlanRequest(BaseModel):
    recipes: list[PlannedRecipe] = Field(min_length=1, max_length=30)
    inventory: list[InventoryLevel] = Field(default_factory=list, max_length=1000)
    policy_id: str | None = Field(default=None, max_length=64)
    buyer_name: str | None = Field(default=None, max_length=120)


class EventPlanLine(BaseModel):
    product_intent: str  # the ingredient the recipes call for
    quantity: float
    unit: Literal["L", "kg", "pcs"]
    required_by: date
    recipe_names: list[str]
    status: Literal["in_time", "substitute", "unsourceable"]
    reason: str
    # what to order (the ingredient itself, or its recipe substitute); None when unsourceable
    order_intent: str | None = None
    order_quantity: float | None = None
    order_unit: Literal["L", "kg", "pcs"] | None = None
    offer: Offer | None = None
    substitution_note: str | None = None


class EventPlanResponse(BaseModel):
    lines: list[EventPlanLine]
    summary: str
