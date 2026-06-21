"""Join discovery + verification for the cartographer (#26b)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.profile_dataset import (
    ProfileDatasetTool,
    _ColumnInfo,
    _TableProfile,
)
from labrat.agent.tools.profile_dataset import (
    _Output as ProfileOutput,
)
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import _candidate_joins, discover_joins


@pytest.fixture()
def ctx(ecommerce_db: Path) -> Iterator[ToolContext]:
    conn = DuckDBConnection(ecommerce_db, read_only=True)
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


def _tp(name: str, cols: list[str], fks: list[str] | None = None) -> _TableProfile:
    return _TableProfile(
        name=name,
        schema_name="main",
        row_count=10,
        columns=[_ColumnInfo(name=c, data_type="INTEGER", nullable=True) for c in cols],
        foreign_keys=fks or [],
    )


def test_candidate_joins_includes_declared_fk_with_nonconventional_name() -> None:
    profile = ProfileOutput(
        database="d",
        tables_total=2,
        tables_profiled=2,
        tables=[
            _tp("dept", ["dept_key", "name"]),
            _tp("emp", ["id", "works_in"], fks=["works_in -> dept.dept_key"]),
        ],
    )
    cands = _candidate_joins(profile)
    assert ("emp", "works_in", "dept", "dept_key") in cands  # only the FK path finds this


def test_candidate_joins_excludes_self_referential_fk() -> None:
    profile = ProfileOutput(
        database="d",
        tables_total=1,
        tables_profiled=1,
        tables=[_tp("node", ["id", "parent"], fks=["parent -> node.id"])],
    )
    assert _candidate_joins(profile) == []  # self-join excluded


def test_candidate_joins_name_heuristic_still_works() -> None:
    profile = ProfileOutput(
        database="d",
        tables_total=2,
        tables_profiled=2,
        tables=[
            _tp("customers", ["customer_id", "name"]),
            _tp("orders", ["order_id", "customer_id"]),
        ],
    )
    cands = _candidate_joins(profile)
    assert ("orders", "customer_id", "customers", "customer_id") in cands
