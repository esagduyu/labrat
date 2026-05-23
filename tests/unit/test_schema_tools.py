"""Tests for schema exploration tools (M12)."""

from __future__ import annotations

from pathlib import Path

import pytest

from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.agent.tools.column_stats import ColumnStatsTool
from labrat.agent.tools.describe_table import DescribeTableTool
from labrat.agent.tools.list_tables import ListTablesTool
from labrat.agent.tools.sample_rows import SampleRowsTool
from labrat.agent.tools.search_columns import SearchColumnsTool
from labrat.db.duckdb_engine import DuckDBConnection

FIXTURE_DB = Path(__file__).parent.parent / "fixtures" / "sample_dbs" / "test.duckdb"


@pytest.fixture()
def ctx() -> ToolContext:  # type: ignore[misc]
    conn = DuckDBConnection(FIXTURE_DB, read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    yield ToolContext(connection=conn, catalog=catalog)  # type: ignore[misc]
    conn.disconnect()


# ── list_tables ────────────────────────────────────────────────────────────────


async def test_list_tables_returns_all_tables(ctx: ToolContext) -> None:
    """list_tables() with no filter returns all tables including orders, users, regions."""
    tool = ListTablesTool()
    result = await tool.execute(ctx, tool.input_model())
    names = {t.name for t in result.tables}
    assert {"orders", "users", "regions"}.issubset(names)


async def test_list_tables_schema_filter(ctx: ToolContext) -> None:
    """list_tables(schema='main') returns only tables from that schema."""
    tool = ListTablesTool()
    result = await tool.execute(ctx, tool.input_model(schema_filter="main"))
    assert len(result.tables) > 0


async def test_list_tables_column_count(ctx: ToolContext) -> None:
    """list_tables() includes column_count for each table."""
    tool = ListTablesTool()
    result = await tool.execute(ctx, tool.input_model())
    orders = next(t for t in result.tables if t.name == "orders")
    assert orders.column_count > 0


# ── describe_table ────────────────────────────────────────────────────────────


async def test_describe_table_columns(ctx: ToolContext) -> None:
    """describe_table('orders') includes expected columns with types."""
    tool = DescribeTableTool()
    result = await tool.execute(ctx, tool.input_model(table="orders"))
    col_names = [c.name for c in result.columns]
    assert "total_amount" in col_names
    assert "status" in col_names


async def test_describe_table_includes_nullability(ctx: ToolContext) -> None:
    """describe_table() records nullable flag per column."""
    tool = DescribeTableTool()
    result = await tool.execute(ctx, tool.input_model(table="orders"))
    assert all(hasattr(c, "nullable") for c in result.columns)


async def test_describe_table_unknown_table(ctx: ToolContext) -> None:
    """describe_table() with an unknown table raises ValueError."""
    tool = DescribeTableTool()
    with pytest.raises(ValueError, match="not found"):
        await tool.execute(ctx, tool.input_model(table="no_such_table_xyz"))


async def test_describe_table_fk_present(ctx: ToolContext) -> None:
    """describe_table('orders') exposes foreign_keys list (may be empty)."""
    tool = DescribeTableTool()
    result = await tool.execute(ctx, tool.input_model(table="orders"))
    assert isinstance(result.foreign_keys, list)


# ── sample_rows ───────────────────────────────────────────────────────────────


async def test_sample_rows_returns_n_rows(ctx: ToolContext) -> None:
    """sample_rows() returns exactly n rows."""
    tool = SampleRowsTool()
    result = await tool.execute(ctx, tool.input_model(table="orders", n=3))
    assert result.row_count == 3
    assert len(result.rows) == 3


async def test_sample_rows_includes_column_names(ctx: ToolContext) -> None:
    """sample_rows() includes column names in the output."""
    tool = SampleRowsTool()
    result = await tool.execute(ctx, tool.input_model(table="orders", n=1))
    assert "total_amount" in result.columns


async def test_sample_rows_default_n(ctx: ToolContext) -> None:
    """sample_rows() defaults to 10 rows."""
    tool = SampleRowsTool()
    args = tool.input_model(table="orders")
    assert args.n == 10


# ── column_stats ──────────────────────────────────────────────────────────────


async def test_column_stats_total_amount(ctx: ToolContext) -> None:
    """column_stats() on total_amount returns non-null stats."""
    tool = ColumnStatsTool()
    result = await tool.execute(ctx, tool.input_model(table="orders", column="total_amount"))
    assert result.null_count == 0
    assert result.distinct_count > 0
    assert result.min_value is not None
    assert result.max_value is not None


async def test_column_stats_column_name(ctx: ToolContext) -> None:
    """column_stats() output carries the queried column name."""
    tool = ColumnStatsTool()
    result = await tool.execute(ctx, tool.input_model(table="orders", column="status"))
    assert result.column_name == "status"


# ── search_columns ────────────────────────────────────────────────────────────


async def test_search_columns_finds_amount(ctx: ToolContext) -> None:
    """search_columns('amount') matches total_amount in orders."""
    tool = SearchColumnsTool()
    result = await tool.execute(ctx, tool.input_model(keyword="amount"))
    col_names = [m.column_name for m in result.matches]
    assert "total_amount" in col_names


async def test_search_columns_no_match(ctx: ToolContext) -> None:
    """search_columns() returns empty matches for nonsense keyword."""
    tool = SearchColumnsTool()
    result = await tool.execute(ctx, tool.input_model(keyword="zzz_no_match_zzz"))
    assert len(result.matches) == 0


async def test_search_columns_keyword_in_output(ctx: ToolContext) -> None:
    """search_columns() echoes the keyword in the output."""
    tool = SearchColumnsTool()
    result = await tool.execute(ctx, tool.input_model(keyword="user"))
    assert result.keyword == "user"


# ── registry integration ──────────────────────────────────────────────────────


def test_all_tools_register_without_conflict() -> None:
    """All five exploration tools can be registered together."""
    registry = ToolRegistry()
    for tool in [
        ListTablesTool(),
        DescribeTableTool(),
        SampleRowsTool(),
        ColumnStatsTool(),
        SearchColumnsTool(),
    ]:
        registry.register(tool)
    names = {t.name for t in registry.tools}
    expected = {"list_tables", "describe_table", "sample_rows", "column_stats", "search_columns"}
    assert names == expected


def test_all_schemas_are_valid_anthropic_format() -> None:
    """Every tool's Anthropic schema has the required structure."""
    registry = ToolRegistry()
    for tool in [
        ListTablesTool(),
        DescribeTableTool(),
        SampleRowsTool(),
        ColumnStatsTool(),
        SearchColumnsTool(),
    ]:
        registry.register(tool)
    for schema in registry.to_anthropic_schemas():
        assert "name" in schema
        assert "description" in schema
        assert schema["input_schema"]["type"] == "object"
