"""MCP transports for the buyer agent."""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from mcp_server import server


class LocalMCPTools:
    """In-process transport for unit tests; production demos use ``StdioMCPTools``."""

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


class StdioMCPTools:
    """A real stdio MCP client for the T9 server, used by the runnable demo."""

    def __init__(self, command: str | None = None, base_url: str | None = None) -> None:
        self._command = command or sys.executable
        self._base_url = base_url
        self._streams: Any = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "StdioMCPTools":
        env = {**os.environ, **({"AIMO_API_URL": self._base_url} if self._base_url else {})}
        params = StdioServerParameters(command=self._command, args=["-m", "mcp_server.server"], env=env)
        self._streams = stdio_client(params)
        read, write = await self._streams.__aenter__()
        self._session = ClientSession(read, write)
        await self._session.__aenter__()
        await self._session.initialize()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        assert self._session is not None and self._streams is not None
        await self._session.__aexit__(exc_type, exc, traceback)
        await self._streams.__aexit__(exc_type, exc, traceback)
        self._session = None
        self._streams = None

    async def call(self, name: str, **arguments: Any) -> Any:
        if self._session is None:
            raise RuntimeError("Open StdioMCPTools with 'async with' before calling tools")
        payload = {key: value.model_dump(mode="json") if hasattr(value, "model_dump") else value
                   for key, value in arguments.items()}
        result = await self._session.call_tool(name, payload)
        if result.isError:
            raise RuntimeError("MCP tool failed: " + "; ".join(getattr(item, "text", str(item)) for item in result.content))
        text = next((item.text for item in result.content if hasattr(item, "text")), None)
        if text is None:
            raise RuntimeError(f"MCP tool {name} returned no text content")
        return json.loads(text)
