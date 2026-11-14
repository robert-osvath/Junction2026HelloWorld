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


def run_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("Test double unexpectedly required an event loop")


class Tools:
    """Records calls; the backend (not the agent) picks the product."""

    def __init__(self, status="placed"):
        self.calls, self.status = [], status

    async def call(self, name, **arguments):
        self.calls.append((name, arguments))
        need = arguments["need"]
        return {"id": "order-1", "mandate_id": arguments["mandate_id"], "need": need.model_dump(mode="json"),
                "lines": [], "status": self.status, "confirmation_no": "AIMO-SIM-1"}


def test_buyer_states_the_need_and_the_backend_orders():
    tools = Tools()
    result = run_immediate(BuyerAgent(tools).run("1,000 L lactose-free milk by Monday", today=date(2026, 11, 14)))
    assert result.status == "placed" and "confirmation" in result.message
    assert [name for name, _ in tools.calls] == ["order_from_need"]
    args = tools.calls[0][1]
    assert args["mandate_id"] == "mandate-hotel-001" and args["simulate_out_of_stock"] is False
    assert args["need"].quantity == 1000 and args["need"].unit == "L"
    assert args["need"].product_intent == "lactose-free milk"


def test_out_of_stock_phrase_sets_the_demo_switch():
    tools = Tools("substitutes_proposed")
    result = run_immediate(BuyerAgent(tools).run("1,000 L lactose-free milk by Monday; first choice is out of stock",
                                                 today=date(2026, 11, 14)))
    assert tools.calls[0][1]["simulate_out_of_stock"] is True and result.status == "substitutes_proposed"


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


def test_dict_results_are_not_unwrapped():
    from types import SimpleNamespace as NS
    from agent.mcp_tools import decode_tool_result
    assert decode_tool_result("t", NS(structuredContent={"result": {"id": "x"}}, content=[])) == {"result": {"id": "x"}}
    assert decode_tool_result("t", NS(structuredContent={"result": []}, content=[])) == []


def test_quantity_parsing_handles_thousands_and_decimal_commas():
    from agent.buyer import parse_need
    q = lambda s: parse_need(f"{s} lactose-free milk by Monday", today=date(2026, 11, 14)).quantity
    assert q("1,000 L") == 1000 and q("1,000.5 L") == 1000.5 and q("1000 L") == 1000
    assert q("1,5 L") == 1.5 and q("2,25 kg") == 2.25 and q("1.5 L") == 1.5 and q("12 pcs") == 12
    try:
        parse_need("1,000,5 L milk")
    except ValueError as exc:
        assert "Ambiguous" in str(exc)
    else:
        raise AssertionError("expected an ambiguous-quantity error")
