"""MCP catalog server discovery helpers (M30).

Reads per-profile catalog config and returns ready MCPCatalogClient instances.
"""

from __future__ import annotations

from labrat.catalog.mcp.client import MCPCatalogClient


def client_from_config(endpoint: str, auth_token: str | None = None) -> MCPCatalogClient:
    """Construct an MCPCatalogClient from config values."""
    return MCPCatalogClient(endpoint=endpoint, auth_token=auth_token)
