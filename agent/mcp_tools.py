"""Adapter that lets the buyer agent invoke the local T9 MCP tool handlers."""
from __future__ import annotations

from typing import Any

from mcp_server import server


class LocalMCPTools:
    """Use the local MCP server's public tool handlers for the one-command demo."""

    _handlers = {
        "search_products": server.search_products,
        "get_offers": server.get_offers,
        "place_order": server.place_order,
        "get_order_status": server.get_order_status,
        "list_substitutes": server.list_substitutes,
    }

    async def call(self, name: str, **arguments: Any) -> Any:
        try:
            handler = self._handlers[name]
        except KeyError as exc:
            raise ValueError(f"Unknown MCP tool: {name}") from exc
        return await handler(**arguments)
