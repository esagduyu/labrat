"""Deterministic skeleton builders for the cartographer (#26b)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.profile_dataset import ProfileDatasetTool
from labrat.agent.tools.profile_dataset import _Output as ProfileOutput
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import build_dimensions, build_key_tables, build_quick_reference


@pytest.fixture()
def ctx(ecommerce_db: Path) -> Iterator[ToolContext]:
    conn = DuckDBConnection(ecommerce_db, read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    yield ToolContext(connection=conn, catalog=catalog)
    conn.disconnect()


async def _profile(ctx: ToolContext) -> ProfileOutput:
    tool = ProfileDatasetTool()
    out = await tool.execute(ctx, tool.input_model(sample_rows=0))
    assert isinstance(out, ProfileOutput)
    return out


async def test_quick_reference_lists_tables_and_grain(ctx: ToolContext) -> None:
    qr = build_quick_reference(await _profile(ctx))
    assert qr.source == "verified"
    assert qr.heading == "Quick Reference"
    assert "orders" in qr.body
    assert "rows" in qr.body


async def test_key_tables_lists_columns(ctx: ToolContext) -> None:
    kt = build_key_tables(await _profile(ctx), [])
    assert kt.source == "verified"
    assert "customer_id" in kt.body
    assert "total_amount" in kt.body


async def test_dimensions_lists_low_cardinality_skips_high(
    ctx: ToolContext, ecommerce_db: Path
) -> None:
    conn = DuckDBConnection(ecommerce_db, read_only=True)
    conn.connect()
    try:
        dims = build_dimensions(await _profile(ctx), conn, cap=25)
    finally:
        conn.disconnect()
    assert dims.source == "verified"
    assert "status" in dims.body  # low-cardinality enum is listed
    assert "email" not in dims.body  # high-cardinality column is skipped
