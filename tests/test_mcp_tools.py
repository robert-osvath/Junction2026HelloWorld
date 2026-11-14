from datetime import date

from contracts import Need, OffersRequest
from mcp_server import server
from mcp_server.server import mcp


def run_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("Test double unexpectedly required an event loop")


def test_server_registers_the_ticket_tools():
    assert set(mcp._tool_manager._tools) == {"search_products", "get_offers", "place_order", "get_order_status", "list_substitutes"}


def test_search_forwards_quantity_aware_query(monkeypatch):
    seen = {}

    class API:
        async def request(self, method, path, *, params=None, body=None):
            seen.update(method=method, path=path, params=params, body=body)
            return []

    monkeypatch.setattr(server, "_api", lambda: API())
    assert run_immediate(server.search_products("milk", quantity=1000, unit="L")) == []
    assert seen == {"method": "GET", "path": "/catalog/search", "params": {
        "q": "milk", "claims": "", "exclude_allergens": "", "category": "",
        "limit": 20, "quantity": 1000, "unit": "L",
    }, "body": None}


def test_get_offers_serializes_shared_request_model(monkeypatch):
    seen = {}

    class API:
        async def request(self, method, path, *, params=None, body=None):
            seen.update(method=method, path=path, body=body)
            return []

    need = Need(text="milk", product_intent="milk", quantity=1000, unit="L", deliver_by=date(2026, 11, 17))
    request = OffersRequest(need=need, product_ids=["p1"], delivery_date=date(2026, 11, 17))
    monkeypatch.setattr(server, "_api", lambda: API())
    assert run_immediate(server.get_offers(request)) == []
    assert seen == {"method": "POST", "path": "/offers", "body": {
        "need": {"text": "milk", "product_intent": "milk", "quantity": 1000.0,
                 "unit": "L", "deliver_by": "2026-11-17", "extra_constraints": []},
        "product_ids": ["p1"], "delivery_date": "2026-11-17",
    }}
