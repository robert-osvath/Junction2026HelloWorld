from mcp_server.server import mcp


def test_server_registers_the_ticket_tools():
    assert set(mcp._tool_manager._tools) == {"search_products", "get_offers", "place_order", "get_order_status", "list_substitutes"}
