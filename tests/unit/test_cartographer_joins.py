"""Join discovery + verification for the cartographer (#26b)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.profile_dataset import ProfileDatasetTool
from labrat.agent.tools.profile_dataset import _Output as ProfileOutput
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import discover_joins

_FIXTURE = "tests/fixtures/sample_dbs/ecommerce.duckdb"


@pytest.fixture()
def ctx() -> Iterator[ToolContext]:
    conn = DuckDBConnection(Path(_FIXTURE), read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    yield ToolContext(connection=conn, catalog=catalog, primary="primary")
    conn.disconnect()


async def _profile(ctx: ToolContext) -> ProfileOutput:
    tool = ProfileDatasetTool()
    out = await tool.execute(ctx, tool.input_model(sample_rows=0))
    assert isinstance(out, ProfileOutput)
    return out


async def test_discovers_real_cross_table_joins(ctx: ToolContext) -> None:
    joins = await discover_joins(ctx, await _profile(ctx), database="primary")
    pairs = {(j.left, j.right) for j in joins}
    assert ("orders.customer_id", "customers.customer_id") in pairs
    assert ("orders.product_id", "products.product_id") in pairs
    assert ("events.customer_id", "customers.customer_id") in pairs
    # all kept joins are mechanically valid
    assert all(j.match_rate >= 0.95 for j in joins)


async def test_excludes_self_joins(ctx: ToolContext) -> None:
    joins = await discover_joins(ctx, await _profile(ctx), database="primary")
    for j in joins:
        assert j.left.split(".")[0] != j.right.split(".")[0]  # no <table>_id -> same table
