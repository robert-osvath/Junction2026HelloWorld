"""MCP tools wrapping the Trusted Trade Agents REST API."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from contracts import OrderRequest, OffersRequest, SubstitutesRequest
from mcp_server.client import AimoAPI

mcp = FastMCP("Trusted Trade Agents", instructions="Use these tools to source and place Aimo orders.")


def _api() -> AimoAPI:
    return AimoAPI()


@mcp.tool()
async def search_products(
    query: str = "", claims: str = "", exclude_allergens: str = "", category: str = "",
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    quantity: Annotated[float | None, Field(gt=0, allow_inf_nan=False)] = None,
    unit: Literal["L", "kg", "pcs"] | None = None,
) -> list[dict[str, Any]]:
    """Find Aimo catalog products before requesting offers."""
    return await _api().request("GET", "/catalog/search", params={
        "q": query, "claims": claims, "exclude_allergens": exclude_allergens,
        "category": category, "limit": limit, "quantity": quantity, "unit": unit,
    })


@mcp.tool()
async def get_offers(request: OffersRequest) -> list[dict[str, Any]]:
    """Get price and availability-backed offers for shortlisted products."""
    return await _api().request("POST", "/offers", body=request.model_dump(mode="json"))


@mcp.tool()
async def place_order(request: OrderRequest) -> dict[str, Any]:
    """Create a simulated Aimo order."""
    return await _api().request("POST", "/orders", body=request.model_dump(mode="json"))


@mcp.tool()
async def get_order_status(order_id: str) -> dict[str, Any]:
    """Retrieve an order and its current status."""
    return await _api().request("GET", f"/orders/{order_id}")


@mcp.tool()
async def list_substitutes(request: SubstitutesRequest) -> list[dict[str, Any]]:
    """Find ranked substitute offers."""
    return await _api().request("POST", "/substitutes", body=request.model_dump(mode="json"))


if __name__ == "__main__":
    mcp.run(transport="stdio")
