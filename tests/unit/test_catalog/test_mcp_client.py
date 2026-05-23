"""Tests for MCP catalog client (M30) — fully mocked."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from labrat.catalog.base import CatalogEntry


def _make_entry(name: str) -> CatalogEntry:
    return CatalogEntry(
        name=name,
        schema_name="public",
        description=f"Description for {name}",
        columns={},
        tags=[],
        upstream=[],
        downstream=[],
        row_count=None,
    )


@pytest.mark.asyncio
async def test_mcp_client_search_returns_entries() -> None:
    from labrat.catalog.mcp.client import MCPCatalogClient

    client = MCPCatalogClient(endpoint="http://fake-mcp/", auth_token=None)
    mock_result = [_make_entry("orders")]
    with patch.object(client, "_call_tool", new=AsyncMock(return_value=mock_result)):
        results = await client.search("orders")
    assert len(results) == 1
    assert results[0].name == "orders"


@pytest.mark.asyncio
async def test_mcp_client_describe_returns_entry() -> None:
    from labrat.catalog.mcp.client import MCPCatalogClient

    client = MCPCatalogClient(endpoint="http://fake-mcp/")
    entry = _make_entry("payments")
    with patch.object(client, "_call_tool", new=AsyncMock(return_value=entry)):
        result = await client.describe("payments")
    assert result is not None
    assert result.name == "payments"


@pytest.mark.asyncio
async def test_mcp_client_describe_returns_none_for_missing_table() -> None:
    from labrat.catalog.mcp.client import MCPCatalogClient

    client = MCPCatalogClient(endpoint="http://fake-mcp/")
    with patch.object(client, "_call_tool", new=AsyncMock(return_value=None)):
        result = await client.describe("nonexistent_table")
    assert result is None


@pytest.mark.asyncio
async def test_mcp_client_close_is_idempotent() -> None:
    from labrat.catalog.mcp.client import MCPCatalogClient

    client = MCPCatalogClient(endpoint="http://fake-mcp/")
    await client.close()
    await client.close()  # calling twice must not raise
