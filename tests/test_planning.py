from datetime import date

from app.planning.router import recipe_gaps, standing_order_drift
from contracts import (
    InventoryLevel, PlannedRecipe, RecipeGapRequest, RecipeIngredient,
    StandingOrderDriftRequest, StandingOrderObservation,
)


TODAY = date(2026, 11, 14)


def test_recipe_gaps_only_count_usable_inventory_and_combine_recipes():
    response = recipe_gaps(RecipeGapRequest(
        today=TODAY,
        recipes=[
            PlannedRecipe(name="Breakfast", servings=10, service_date=date(2026, 11, 16), ingredients=[
                RecipeIngredient(product_intent="lactose-free milk", quantity_per_serving=0.2, unit="L", required_claims=["FREE_FROM_LACTOSE"]),
            ]),
            PlannedRecipe(name="Coffee service", servings=5, service_date=date(2026, 11, 15), ingredients=[
                RecipeIngredient(product_intent="lactose-free milk", quantity_per_serving=0.1, unit="L"),
            ]),
        ],
        inventory=[
            InventoryLevel(product_intent="lactose-free milk", quantity=1, unit="L", usable_until=date(2026, 11, 16)),
            InventoryLevel(product_intent="lactose-free milk", quantity=9, unit="L", usable_until=date(2026, 11, 13)),
        ],
    ))
    assert len(response.gaps) == 1
    gap = response.gaps[0]
    assert (gap.quantity_needed, gap.quantity_on_hand, gap.quantity_to_order) == (2.5, 1.0, 1.5)
    assert gap.required_by == date(2026, 11, 15)
    assert response.expired_or_unusable_inventory_value == 1


def test_standing_order_drift_requires_review_outside_tolerance():
    response = standing_order_drift(StandingOrderDriftRequest(
        product_intent="oat drink barista", proposed_quantity=15, unit="L", tolerance_pct=0.2,
        history=[StandingOrderObservation(date=date(2026, 10, day), quantity=10) for day in (1, 8, 15, 22)],
    ))
    assert response.status == "review_required"
    assert response.delta_pct == 0.5
