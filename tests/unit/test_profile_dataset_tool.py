"""Tests for the profile_dataset tool (north-star Pillar 1 — Scent generator)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.profile_dataset import ProfileDatasetTool
from labrat.db.catalog import Catalog, Column, ForeignKey, Table
from labrat.db.duckdb_engine import DuckDBConnection


@pytest.fixture(scope="module")
def fixture_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    p = tmp_path_factory.mktemp("profile_dbs") / "profile.duckdb"
    con = duckdb.connect(str(p))
    con.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY, name VARCHAR)")
    con.execute(
        "CREATE TABLE child(id INTEGER, parent_id INTEGER, FOREIGN KEY(parent_id) "
        "REFERENCES parent(id))"
    )
    con.execute("INSERT INTO parent VALUES (1,'a'),(2,'b')")
    con.execute("INSERT INTO child VALUES (1,1),(2,2),(3,1)")
    con.close()
    return p


@pytest.fixture()
def ctx(fixture_db: Path) -> ToolContext:  # type: ignore[misc]
    conn = DuckDBConnection(fixture_db, read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    yield ToolContext(connection=conn, catalog=catalog)  # type: ignore[misc]
    conn.disconnect()


async def test_profiles_all_tables_with_columns_and_rowcount(ctx: ToolContext) -> None:
    tool = ProfileDatasetTool()
    out = await tool.execute(ctx, tool.input_model())

    assert out.tables_total == 2
    assert out.tables_profiled == 2
    assert not out.truncated
    by_name = {t.name: t for t in out.tables}
    assert {"parent", "child"} == set(by_name)

    parent = by_name["parent"]
    assert {c.name for c in parent.columns} == {"id", "name"}
    # row_count comes from the COUNT(*) fallback (DuckDB introspection leaves it None).
    assert parent.row_count == 2
    assert by_name["child"].row_count == 3
    # sample rows are populated by default (sample_rows=3).
    assert len(parent.sample) >= 1
    assert parent.sample_columns == ["id", "name"]


async def test_tables_filter_restricts_output(ctx: ToolContext) -> None:
    tool = ProfileDatasetTool()
    out = await tool.execute(ctx, tool.input_model(tables=["parent"]))
    assert [t.name for t in out.tables] == ["parent"]
    assert out.tables_total == 1


async def test_max_tables_truncates_and_flags(ctx: ToolContext) -> None:
    tool = ProfileDatasetTool()
    out = await tool.execute(ctx, tool.input_model(max_tables=1))
    assert out.tables_total == 2
    assert out.tables_profiled == 1
    assert out.truncated
    assert out.note is not None and "of 2 tables" in out.note


async def test_sample_rows_zero_skips_sampling(ctx: ToolContext) -> None:
    tool = ProfileDatasetTool()
    out = await tool.execute(ctx, tool.input_model(sample_rows=0))
    for t in out.tables:
        assert t.sample == []
        assert t.sample_columns == []
        # structure + counts still present without sampling
        assert t.columns
        assert t.row_count is not None


async def test_empty_catalog_emits_helpful_note(fixture_db: Path) -> None:
    conn = DuckDBConnection(fixture_db, read_only=True)
    conn.connect()
    try:
        empty_ctx = ToolContext(
            connection=conn,  # type: ignore[arg-type]
            catalog=Catalog(database_name="x", schemas=[]),
        )
        tool = ProfileDatasetTool()
        out = await tool.execute(empty_ctx, tool.input_model())
        assert out.tables_total == 0
        assert out.tables == []
        assert out.note is not None and "introspected" in out.note
    finally:
        conn.disconnect()


def test_foreign_keys_are_formatted(ctx: ToolContext) -> None:
    """DuckDB introspection drops FKs, so verify the formatting against a synthetic Table.

    sample_rows=0 + a populated row_count means the connection is never touched."""
    tool = ProfileDatasetTool()
    table = Table(
        name="child",
        schema_name="main",
        columns=[Column(name="parent_id", data_type="INTEGER", nullable=True)],
        foreign_keys=[
            ForeignKey(column="parent_id", referenced_table="parent", referenced_column="id")
        ],
        row_count=3,
    )
    conn = ctx.connections["primary"]
    profile = tool._profile_table(conn, table, sample_rows=0)  # type: ignore[arg-type]
    assert profile.foreign_keys == ["parent_id -> parent.id"]
    assert profile.row_count == 3


def test_profile_dataset_in_default_registry() -> None:
    registry = build_data_tools_registry()
    names = {t.name for t in registry.tools}
    assert "profile_dataset" in names
