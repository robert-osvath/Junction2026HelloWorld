"""MCP tools wrapping the Trusted Trade Agents REST API."""
from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_server.client import AimoAPI

mcp = FastMCP("Trusted Trade Agents", instructions="Use these tools to source and place Aimo orders.")


def _api() -> AimoAPI:
    return AimoAPI()


@mcp.tool()
async def search_products(query: str = "", claims: str = "", exclude_allergens: str = "", category: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """Find Aimo catalog products before requesting offers."""
    return await _api().request("GET", "/catalog/search", params={"q": query, "claims": claims, "exclude_allergens": exclude_allergens, "category": category, "limit": limit})


@mcp.tool()
async def get_offers(need: dict[str, Any], product_ids: list[str], delivery_date: str) -> list[dict[str, Any]]:
    """Get price and availability-backed offers for shortlisted products."""
    return await _api().request("POST", "/offers", body={"need": need, "product_ids": product_ids, "delivery_date": delivery_date})


@mcp.tool()
async def place_order(mandate_id: str, need: dict[str, Any], lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Create a simulated Aimo order."""
    return await _api().request("POST", "/orders", body={"mandate_id": mandate_id, "need": need, "lines": lines})


@mcp.tool()
async def get_order_status(order_id: str) -> dict[str, Any]:
    """Retrieve an order and its current status."""
    return await _api().request("GET", f"/orders/{order_id}")


@mcp.tool()
async def list_substitutes(mandate: dict[str, Any], need: dict[str, Any], offer: dict[str, Any]) -> list[dict[str, Any]]:
    """Find ranked substitute offers."""
    return await _api().request("POST", "/substitutes", body={"mandate": mandate, "need": need, "offer": offer})


if __name__ == "__main__":
    mcp.run(transport="stdio")
