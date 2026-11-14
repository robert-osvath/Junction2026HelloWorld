from datetime import date

from agent.buyer import BuyerAgent, parse_need


def test_parses_milk_request_for_next_monday():
    need = parse_need("1,000 L lactose-free milk by Monday", today=date(2026, 11, 14))
    assert need.quantity == 1000
    assert need.unit == "L"
    assert need.product_intent == "lactose-free milk"
    assert need.deliver_by == date(2026, 11, 16)


def test_requires_a_quantity_and_unit():
    try:
        parse_need("lactose-free milk by Monday")
    except ValueError as exc:
        assert "quantity and unit" in str(exc)
    else:
        raise AssertionError("Expected request validation to fail")


def test_buyer_runs_search_offers_and_order_through_mcp():
    calls = []

    class Tools:
        async def call(self, name, **arguments):
            calls.append((name, arguments))
            if name == "search_products":
                return [{"product_id": "milk-1"}]
            if name == "get_offers":
                return [{
                    "product_id": "milk-1", "name": "Lactose-free milk", "sales_unit": "KI",
                    "units_needed": 50, "unit_price_eur": 20, "comparison_price_eur": 1,
                    "comparison_unit": "L", "total_eur": 1000, "available_date": "2026-11-16",
                    "closing_time": None, "sales_block": False, "logistic_model": "Stock",
                    "evidence": {"stock": "verified", "allergens": "declared"},
                }]
            assert name == "place_order"
            request = arguments["request"]
            return {"id": "order-1", "mandate_id": request.mandate_id,
                    "need": request.need.model_dump(mode="json"),
                    "lines": [line.model_dump(mode="json") for line in request.lines],
                    "status": "placed", "confirmation_no": "AIMO-SIM-1"}

    def run_immediate(coroutine):
        try:
            coroutine.send(None)
        except StopIteration as result:
            return result.value
        raise AssertionError("Test double unexpectedly required an event loop")

    result = run_immediate(BuyerAgent(Tools()).run("1,000 L lactose-free milk by Monday", today=date(2026, 11, 14)))
    assert result.status == "placed" and "confirmation" in result.message
    assert [name for name, _ in calls] == ["search_products", "get_offers", "place_order"]
    assert calls[0][1]["quantity"] == 1000 and calls[0][1]["unit"] == "L"
    assert calls[1][1]["request"].product_ids == ["milk-1"]
