"""Tests for the load_file tool (north-star Pillar 1 — connect & ingest)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.load_file import LoadFileTool
from labrat.db.catalog import Catalog
from labrat.db.duckdb_engine import DuckDBConnection


def _mem_ctx() -> tuple[ToolContext, DuckDBConnection]:
    conn = DuckDBConnection(":memory:", read_only=False)
    conn.connect()
    ctx = ToolContext(
        connection=conn,  # type: ignore[arg-type]
        catalog=Catalog(database_name="mem", schemas=[]),
    )
    return ctx, conn


async def test_load_csv_creates_queryable_table(tmp_path: Path) -> None:
    csv = tmp_path / "data.csv"
    csv.write_text("a,b\n1,x\n2,y\n3,z\n")
    ctx, conn = _mem_ctx()
    try:
        tool = LoadFileTool()
        out = await tool.execute(ctx, tool.input_model(path=str(csv), table_name="t"))
        assert out.ok
        assert out.row_count == 3
        df = conn.execute("SELECT COUNT(*) AS n FROM t")
        assert int(df.item(0, 0)) == 3
    finally:
        conn.disconnect()


async def test_load_json_and_parquet(tmp_path: Path) -> None:
    jsonl = tmp_path / "data.json"
    jsonl.write_text('{"x": 1}\n{"x": 2}\n')
    parquet = tmp_path / "data.parquet"
    pl.DataFrame({"k": [1, 2, 3, 4]}).write_parquet(parquet)

    ctx, conn = _mem_ctx()
    try:
        tool = LoadFileTool()
        out_json = await tool.execute(ctx, tool.input_model(path=str(jsonl), table_name="j"))
        assert out_json.ok and out_json.row_count == 2
        out_pq = await tool.execute(ctx, tool.input_model(path=str(parquet), table_name="p"))
        assert out_pq.ok and out_pq.row_count == 4
    finally:
        conn.disconnect()


async def test_unsupported_format_returns_error(tmp_path: Path) -> None:
    weird = tmp_path / "data.xyz"
    weird.write_text("nope")
    ctx, conn = _mem_ctx()
    try:
        tool = LoadFileTool()
        out = await tool.execute(ctx, tool.input_model(path=str(weird), table_name="t"))
        assert not out.ok
        assert "Unsupported file format" in out.message
    finally:
        conn.disconnect()


async def test_invalid_table_name_returns_error(tmp_path: Path) -> None:
    csv = tmp_path / "data.csv"
    csv.write_text("a\n1\n")
    ctx, conn = _mem_ctx()
    try:
        tool = LoadFileTool()
        out = await tool.execute(ctx, tool.input_model(path=str(csv), table_name="bad-name"))
        assert not out.ok
        assert "alphanumeric" in out.message
    finally:
        conn.disconnect()


async def test_works_against_read_only_database_via_temp_table(tmp_path: Path) -> None:
    """A read-only DuckDB still accepts load_file because it creates a TEMP table."""
    db = tmp_path / "ro.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE existing(id INTEGER)")
    con.close()
    csv = tmp_path / "more.csv"
    csv.write_text("v\n10\n20\n")

    conn = DuckDBConnection(db, read_only=True)
    conn.connect()
    try:
        ctx = ToolContext(
            connection=conn,  # type: ignore[arg-type]
            catalog=Catalog(database_name="ro", schemas=[]),
        )
        tool = LoadFileTool()
        out = await tool.execute(ctx, tool.input_model(path=str(csv), table_name="more"))
        assert out.ok and out.row_count == 2
    finally:
        conn.disconnect()


async def test_non_duckdb_connection_returns_error(tmp_path: Path) -> None:
    csv = tmp_path / "data.csv"
    csv.write_text("a\n1\n")
    ctx = ToolContext(
        connections={"primary": object()},
        catalogs={"primary": Catalog(database_name="x", schemas=[])},
        primary="primary",
    )
    tool = LoadFileTool()
    out = await tool.execute(ctx, tool.input_model(path=str(csv), table_name="t"))
    assert not out.ok
    assert "requires a DuckDB" in out.message


def test_load_file_in_default_registry() -> None:
    names = {t.name for t in build_data_tools_registry().tools}
    assert "load_file" in names
