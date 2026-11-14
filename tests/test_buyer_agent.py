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


def _offer(pid, total, *, stock="estimated", blocked=False):
    from contracts import Offer
    return Offer.model_validate({
        "product_id": pid, "name": pid, "sales_unit": "KI", "units_needed": 50, "unit_price_eur": total / 50,
        "comparison_price_eur": 1.5, "comparison_unit": "L", "total_eur": total, "available_date": "2026-11-16",
        "closing_time": None, "sales_block": blocked, "logistic_model": "Terminal" if stock == "estimated" else "Stock",
        "evidence": {"stock": stock, "allergens": "declared"},
    })


def test_select_offer_prefers_orderable_then_stock_evidence_then_price():
    from agent.buyer import select_offer
    assert select_offer([_offer("a", 1565.5), _offer("b", 1314.72), _offer("c", 1332.0)]).product_id == "b"
    assert select_offer([_offer("cheap", 900, blocked=True), _offer("ok", 1500)]).product_id == "ok"
    assert select_offer([_offer("short", 900, stock="none"), _offer("est", 1500)]).product_id == "est"
    assert select_offer([_offer("est", 900), _offer("ver", 1500, stock="verified")]).product_id == "ver"


def test_buyer_orders_one_offer_and_searches_with_mandate_rules():
    calls = []
    offers = [_offer("p1", 1565.5), _offer("p2", 1314.72), _offer("p3", 1875.0)]

    class Tools:
        async def call(self, name, **arguments):
            calls.append((name, arguments))
            if name == "search_products":
                return [{"product_id": o.product_id} for o in offers]
            if name == "get_offers":
                return [o.model_dump(mode="json") for o in offers]
            request = arguments["request"]
            return {"id": "order-1", "mandate_id": request.mandate_id, "need": request.need.model_dump(mode="json"),
                    "lines": [line.model_dump(mode="json") for line in request.lines], "status": "placed"}

    def run_immediate(coroutine):
        try:
            coroutine.send(None)
        except StopIteration as result:
            return result.value
        raise AssertionError("Test double unexpectedly required an event loop")

    run_immediate(BuyerAgent(Tools()).run("1,000 L lactose-free milk by Monday", today=date(2026, 11, 14)))
    search_args, order_args = calls[0][1], calls[2][1]
    assert search_args["claims"] == "FREE_FROM_LACTOSE" and search_args["exclude_allergens"] == ""
    assert [line.product_id for line in order_args["request"].lines] == ["p2"]  # one line, the cheapest

    calls.clear()
    run_immediate(BuyerAgent(Tools()).run("1,000 L lactose-free milk by Monday; first choice is out of stock",
                                          today=date(2026, 11, 14)))
    (line,) = calls[2][1]["request"].lines
    assert line.product_id == "p2" and line.sales_block and line.evidence.stock == "none"


def test_decode_tool_result_keeps_whole_lists():
    from types import SimpleNamespace as NS
    from agent.mcp_tools import decode_tool_result
    items = [NS(text='{"product_id": "a"}'), NS(text='{"product_id": "b"}')]
    # FastMCP: one text item per list element + structuredContent {"result": [...]}
    assert decode_tool_result("t", NS(structuredContent={"result": [{"product_id": "a"}, {"product_id": "b"}]},
                                      content=items)) == [{"product_id": "a"}, {"product_id": "b"}]
    assert decode_tool_result("t", NS(structuredContent=None, content=items)) == [{"product_id": "a"}, {"product_id": "b"}]
    assert decode_tool_result("t", NS(structuredContent={"id": "o1", "status": "placed"}, content=[])) == {"id": "o1", "status": "placed"}
    assert decode_tool_result("t", NS(structuredContent=None, content=[NS(text='{"id": "o1"}')])) == {"id": "o1"}


def test_select_offer_stays_within_budget_and_dict_results_are_not_unwrapped():
    from types import SimpleNamespace as NS
    from agent.buyer import select_offer
    from agent.mcp_tools import decode_tool_result
    # a verified offer over budget must not beat an in-budget estimated one
    assert select_offer([_offer("ver", 2600, stock="verified"), _offer("est", 1500)], budget_eur=2500).product_id == "est"
    # nothing fits: still choose (the jury explains the rejection)
    assert select_offer([_offer("a", 3000), _offer("b", 2800)], budget_eur=2500).product_id == "b"
    assert decode_tool_result("t", NS(structuredContent={"result": {"id": "x"}}, content=[])) == {"result": {"id": "x"}}
    assert decode_tool_result("t", NS(structuredContent={"result": []}, content=[])) == []
