from datetime import date, datetime

from app.planning.router import HELSINKI, plan_event
from app.planning.substitutions import swaps_for
from contracts import EventPlanRequest, Need, Offer, PlannedRecipe, RecipeIngredient
from app.mocks import load

NOW = datetime(2026, 11, 15, 9, 0, tzinfo=HELSINKI)  # Sunday morning
MONDAY = date(2026, 11, 16)


def offer(name: str, *, closing: str | None = "2026-11-15T17:00:00+02:00", available: date = MONDAY,
          total: float = 10.0, stock: str = "verified", blocked: bool = False) -> Offer:
    return Offer.model_validate(load("offer").model_dump(mode="json") | {
        "name": name, "total_eur": total, "available_date": available.isoformat(), "closing_time": closing,
        "sales_block": blocked, "evidence": {"stock": stock, "allergens": "declared", "reliability": None}})


def request(*ingredients: tuple[str, float, str]) -> EventPlanRequest:
    return EventPlanRequest(recipes=[PlannedRecipe(name="Gala dinner", servings=40, service_date=MONDAY, ingredients=[
        RecipeIngredient(product_intent=i, quantity_per_serving=q, unit=u) for i, q, u in ingredients])])


def catalog(by_intent: dict[str, list[Offer]], seen: list[Need] | None = None):
    def find(need: Need) -> list[Offer]:
        if seen is not None:
            seen.append(need)
        return by_intent.get(need.product_intent, [])
    return find


def test_in_time_picks_cheapest_orderable_offer():
    plan = plan_event(request(("salmon", 0.1, "kg")), catalog({"salmon": [
        offer("Salmon A", total=50), offer("Salmon B", total=40), offer("Salmon late", total=1, available=date(2026, 11, 17)),
    ]}), NOW)
    line = plan.lines[0]
    assert (line.status, line.offer.name, line.order_intent, line.order_quantity) == ("in_time", "Salmon B", "salmon", 4.0)


def test_passed_cutoff_falls_back_to_recipe_substitute_with_scaled_quantity():
    seen: list[Need] = []
    plan = plan_event(request(("butter", 0.025, "kg")), catalog({
        "butter": [offer("Butter", closing="2026-11-12T15:00:00+02:00")],
        "margarine": [offer("Margarine", blocked=True)],
        "rapeseed oil": [offer("Oil")],
    }, seen), NOW)
    line = plan.lines[0]
    assert line.status == "substitute"
    assert (line.order_intent, line.order_quantity, line.order_unit) == ("rapeseed oil", 0.8, "L")
    assert "cut-off" in line.reason and "0.8 L" in line.substitution_note
    assert [n.product_intent for n in seen] == ["butter", "margarine", "rapeseed oil"]
    assert seen[-1].deliver_by == MONDAY


def test_unsourceable_when_nothing_arrives_in_time():
    plan = plan_event(request(("salmon", 0.1, "kg"), ("saffron", 0.001, "kg")), catalog({
        "salmon": [offer("Salmon", available=date(2026, 11, 18))],
        "rainbow trout": [offer("Trout", stock="none")],
    }), NOW)
    by = {line.product_intent: line for line in plan.lines}
    assert by["salmon"].status == by["saffron"].status == "unsourceable"
    assert by["salmon"].offer is None and "2026-11-18" in by["salmon"].reason
    assert "No recipe substitute is known" in by["saffron"].reason
    assert plan.summary.endswith("2 cannot be sourced in time.")


def test_swaps_match_specific_phrases_first_and_never_return_the_same_ingredient():
    assert swaps_for("Buttermilk")[0].intent == "turkish yoghurt"
    assert swaps_for("sour cream")[0].intent == "creme fraiche"
    assert swaps_for("whipping cream 35%")[0].intent == "cooking cream"
    assert all(s.intent != "turkish yoghurt" for s in swaps_for("turkish yoghurt"))
    assert swaps_for("saffron") == []
