"""Kitchen planning: turn planned recipes into auditable buying needs."""
from collections import defaultdict
from statistics import median

from fastapi import APIRouter

from contracts import (
    RecipeGap, RecipeGapRequest, RecipeGapResponse, StandingOrderDriftRequest,
    StandingOrderDriftResponse,
)

router = APIRouter(prefix="/planning", tags=["kitchen planning"])


def _key(intent: str, unit: str) -> tuple[str, str]:
    return intent.strip().casefold(), unit


@router.post("/recipe-gaps", response_model=RecipeGapResponse)
def recipe_gaps(request: RecipeGapRequest) -> RecipeGapResponse:
    """Calculate what recipes need after subtracting only usable inventory."""
    demand: dict[tuple[str, str], dict] = {}
    for recipe in request.recipes:
        for ingredient in recipe.ingredients:
            key = _key(ingredient.product_intent, ingredient.unit)
            row = demand.setdefault(key, {"intent": ingredient.product_intent, "unit": ingredient.unit,
                                          "quantity": 0.0, "claims": set(), "date": recipe.service_date,
                                          "recipes": []})
            row["quantity"] += ingredient.quantity_per_serving * recipe.servings
            row["claims"].update(ingredient.required_claims)
            row["date"] = min(row["date"], recipe.service_date)
            if recipe.name not in row["recipes"]:
                row["recipes"].append(recipe.name)

    on_hand: defaultdict[tuple[str, str], float] = defaultdict(float)
    usable, unusable = 0, 0
    for item in request.inventory:
        if item.usable_until is not None and item.usable_until < request.today:
            unusable += 1
            continue
        on_hand[_key(item.product_intent, item.unit)] += item.quantity
        usable += 1

    gaps: list[RecipeGap] = []
    for key, row in demand.items():
        needed, available = row["quantity"], on_hand[key]
        shortfall = max(0.0, needed - available)
        if shortfall == 0:
            continue
        urgent = row["date"] <= request.today
        gaps.append(RecipeGap(
            product_intent=row["intent"], quantity_needed=round(needed, 3), quantity_on_hand=round(available, 3),
            quantity_to_order=round(shortfall, 3), unit=row["unit"], required_by=row["date"],
            required_claims=sorted(row["claims"]), recipe_names=row["recipes"], urgency="urgent" if urgent else "normal",
            explanation=(f"{shortfall:g} {row['unit']} short for {', '.join(row['recipes'])}; "
                         f"{available:g} {row['unit']} usable inventory is already allocated."),
        ))
    return RecipeGapResponse(gaps=sorted(gaps, key=lambda gap: (gap.required_by, gap.product_intent)),
                             usable_inventory_value=usable, expired_or_unusable_inventory_value=unusable)


@router.post("/standing-order-drift", response_model=StandingOrderDriftResponse)
def standing_order_drift(request: StandingOrderDriftRequest) -> StandingOrderDriftResponse:
    """Detect a material deviation from the recurring-order baseline."""
    baseline = median(item.quantity for item in request.history)
    delta_pct = 0.0 if baseline == 0 and request.proposed_quantity == 0 else (
        1.0 if baseline == 0 else (request.proposed_quantity - baseline) / baseline
    )
    review = abs(delta_pct) > request.tolerance_pct
    return StandingOrderDriftResponse(
        product_intent=request.product_intent, baseline_quantity=baseline,
        proposed_quantity=request.proposed_quantity, delta_pct=round(delta_pct, 4),
        status="review_required" if review else "within_pattern",
        explanation=(f"Proposed {request.proposed_quantity:g} {request.unit} is {abs(delta_pct):.0%} "
                     f"{'above' if delta_pct >= 0 else 'below'} the median recurring quantity of {baseline:g} {request.unit}."
                     if review else f"Proposed quantity is within the {request.tolerance_pct:.0%} standing-order tolerance."),
    )
