"""Kitchen planning: turn planned recipes into auditable buying needs."""
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from statistics import median
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException

from app.catalog import store
from app.catalog.aimo import AimoError, get_client
from app.catalog.offers import get_offers
from app.orders import policies
from app.orders.flow import candidate_ids
from app.planning.substitutions import swaps_for
from contracts import (
    EventPlanLine, EventPlanRequest, EventPlanResponse, Mandate, Need, Offer, OffersRequest, RecipeGap,
    RecipeGapRequest, RecipeGapResponse, StandingOrderDriftRequest, StandingOrderDriftResponse,
)

HELSINKI = ZoneInfo("Europe/Helsinki")

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


MAX_EVENT_INGREDIENTS = 25  # each costs up to 3 live lookups; keeps a plan inside the voice tool's 90 s timeout


class TooManyIngredients(ValueError):
    pass


def _blocker(o: Offer, need: Need, now: datetime) -> str | None:
    """Why this offer cannot be ordered now and arrive by the service date; None when it can."""
    if o.sales_block:
        return f"{o.name} is sales-blocked."
    if o.available_date > need.deliver_by:
        return f"Earliest delivery is {o.available_date.isoformat()}, after {need.deliver_by.isoformat()}."
    if o.closing_time is not None and now >= o.closing_time:
        return f"Order cut-off for {o.name} passed at {o.closing_time.astimezone(HELSINKI):%a %d.%m %H:%M}."
    if o.evidence.stock == "none":
        return f"{o.name} has no stock evidence for this quantity."
    return None


def _source(offers: list[Offer], need: Need, now: datetime) -> tuple[Offer | None, str]:
    """Cheapest offer that arrives in time, else None and why the earliest offer cannot."""
    if not offers:
        return None, (f"No live-priced product found for {need.quantity:g} {need.unit} {need.product_intent} "
                      f"(no catalog match under the policy, or it is sold in another unit).")
    ok = [o for o in offers if _blocker(o, need, now) is None]
    if ok:
        best = min(ok, key=lambda o: o.total_eur)
        return best, f"{best.name} arrives {best.available_date.isoformat()}."
    return None, _blocker(min(offers, key=lambda o: o.available_date), need, now)


def _plan_gap(gap: RecipeGap, find_offers: Callable[[Need], list[Offer]], now: datetime,
              substitutes_allowed: bool) -> EventPlanLine:
    base = dict(product_intent=gap.product_intent, quantity=gap.quantity_to_order, unit=gap.unit,
                required_by=gap.required_by, recipe_names=gap.recipe_names)
    need = Need(text=f"{gap.quantity_to_order:g} {gap.unit} of {gap.product_intent}", product_intent=gap.product_intent,
                quantity=gap.quantity_to_order, unit=gap.unit, deliver_by=gap.required_by,
                extra_constraints=gap.required_claims)
    offer, why = _source(find_offers(need), need, now)
    if offer is not None:
        return EventPlanLine(**base, status="in_time", reason=why, order_intent=gap.product_intent,
                             order_quantity=gap.quantity_to_order, order_unit=gap.unit, offer=offer)
    swaps = swaps_for(gap.product_intent)
    if not substitutes_allowed:
        return EventPlanLine(**base, status="unsourceable", reason=f"{why} Your policy does not allow substitutes.")
    for swap in swaps:
        qty = round(gap.quantity_to_order * swap.ratio, 3)
        sub = need.model_copy(update={"text": f"{qty:g} {swap.unit or gap.unit} of {swap.intent}",
                                      "product_intent": swap.intent, "quantity": qty, "unit": swap.unit or gap.unit})
        offer, _ = _source(find_offers(sub), sub, now)
        if offer is not None:
            return EventPlanLine(**base, status="substitute", reason=f"{why} Recipe substitute instead.",
                                 order_intent=swap.intent, order_quantity=qty, order_unit=sub.unit,
                                 offer=offer, substitution_note=swap.note)
    tail = (" No recipe substitute arrives in time either; change the dish or buy locally." if swaps
            else " No recipe substitute is known; change the dish or buy locally.")
    return EventPlanLine(**base, status="unsourceable", reason=why + tail)


def plan_event(request: EventPlanRequest, find_offers: Callable[[Need], list[Offer]], now: datetime,
               substitutes_allowed: bool = True) -> EventPlanResponse:
    """For a last-minute event: what the recipes need, what arrives in time, and recipe-level
    substitutes for what does not. Nothing is ordered here; ordering re-runs the full jury."""
    gaps = recipe_gaps(RecipeGapRequest(recipes=request.recipes, inventory=request.inventory,
                                        today=now.astimezone(HELSINKI).date())).gaps
    if len(gaps) > MAX_EVENT_INGREDIENTS:
        raise TooManyIngredients(f"{len(gaps)} distinct ingredients; plan at most {MAX_EVENT_INGREDIENTS} at a time.")
    with ThreadPoolExecutor(max_workers=5) as pool:
        lines = list(pool.map(lambda g: _plan_gap(g, find_offers, now, substitutes_allowed), gaps))
    counts = {s: sum(line.status == s for line in lines) for s in ("in_time", "substitute", "unsourceable")}
    summary = (f"{len(lines)} ingredients · {counts['in_time']} in time · {counts['substitute']} via recipe "
               f"substitute · {counts['unsourceable']} cannot be sourced in time.")
    return EventPlanResponse(lines=lines, summary=summary)


def _live_offers(mandate: Mandate) -> Callable[[Need], list[Offer]]:
    def find(need: Need) -> list[Offer]:
        m = mandate.model_copy(update={"required_claims": sorted({*mandate.required_claims, *need.extra_constraints})})
        conn = store.connect()  # per call: sqlite connections are not shared across threads
        try:
            ids = candidate_ids(conn, m, need, None, 5)
            return get_offers(conn, get_client(), OffersRequest(need=need, product_ids=ids,
                                                                delivery_date=need.deliver_by)) if ids else []
        finally:
            conn.close()
    return find


@router.post("/event-plan", response_model=EventPlanResponse)
def event_plan(request: EventPlanRequest) -> EventPlanResponse:
    """Last-minute event: source every recipe ingredient live, flag what cannot arrive in time,
    and propose recipe-level substitutes under the buyer's policy."""
    mandate = policies.resolve(request.policy_id) if request.policy_id else policies.current(request.buyer_name)
    if mandate is None:
        raise HTTPException(status_code=404, detail=f"Unknown policy {request.policy_id!r}.")
    try:
        return plan_event(request, _live_offers(mandate), datetime.now(HELSINKI), mandate.substitutes_allowed)
    except TooManyIngredients as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AimoError as exc:
        raise HTTPException(status_code=502, detail="Valio Aimo API unavailable") from exc
