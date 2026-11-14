"""Small, testable REST client used by the MCP tools."""
from __future__ import annotations

import os
from typing import Any

import httpx


class AimoAPIError(RuntimeError):
    """A concise error safe to surface to an MCP client."""


class AimoAPI:
    def __init__(self, base_url: str | None = None, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = (base_url or os.getenv("AIMO_API_URL") or "http://127.0.0.1:8000").rstrip("/")
        self._client = client

    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> Any:
        owns_client = self._client is None
        client = self._client if self._client is not None else httpx.AsyncClient(base_url=self._base_url, timeout=20.0)
        try:
            response = await client.request(method, path, params=params, json=body)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise AimoAPIError(f"Aimo API {exc.response.status_code} for {method} {path}: {exc.response.text[:500]}") from exc
        except httpx.HTTPError as exc:
            raise AimoAPIError(f"Aimo API unavailable at {self._base_url}: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()
