"""MCP catalog client (M30).

Connects to an MCP catalog server and exposes catalog operations.
All network I/O is behind `_call_tool()` so tests can patch it without a real server.
"""

from __future__ import annotations

from typing import Any

from labrat.catalog.base import CatalogEntry


class MCPCatalogClient:
    """Thin async client that delegates catalog queries to an MCP server."""

    def __init__(self, endpoint: str, auth_token: str | None = None) -> None:
        self._endpoint = endpoint
        self._auth_token = auth_token

    async def search(self, query: str) -> list[CatalogEntry]:
        """Search the catalog for tables matching *query*."""
        result: Any = await self._call_tool("catalog_search", {"query": query})
        if isinstance(result, list):
            return result  # type: ignore[return-value]
        return []

    async def describe(self, table: str) -> CatalogEntry | None:
        """Return catalog metadata for *table*, or None if not found."""
        result: Any = await self._call_tool("catalog_describe", {"table": table})
        if isinstance(result, CatalogEntry):
            return result
        return None

    async def close(self) -> None:
        """Release any open connection. Safe to call multiple times."""

    # ── internal ──────────────────────────────────────────────────────────────

    async def _call_tool(self, tool_name: str, params: dict[str, Any]) -> Any:
        """Send a tool call to the MCP server. Replaced by mocks in tests."""
        raise NotImplementedError(
            f"No live MCP transport configured. Cannot call {tool_name!r} at {self._endpoint!r}."
        )
