"""Deterministic code/name-pair detector (C2)."""

from __future__ import annotations

import duckdb

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.profile_dataset import ProfileDatasetTool
from labrat.agent.tools.profile_dataset import (
    _Output as ProfileOutput,  # pyright: ignore[reportPrivateUsage]
)
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import build_code_name_notes


async def _profile(conn: DuckDBConnection, db: str = "main") -> ProfileOutput:
    ctx = ToolContext(
        connections={db: conn},
        catalogs={db: conn.introspect_catalog()},
        primary=db,
    )
    tool = ProfileDatasetTool()
    return await tool.execute(ctx, tool.input_model(database=db, sample_rows=0, max_tables=10_000))


def _conn(path: str, ddl: list[str]) -> DuckDBConnection:
    raw = duckdb.connect(path)
    for stmt in ddl:
        raw.execute(stmt)
    raw.close()
    c = DuckDBConnection(path=path, read_only=False)
    c.connect()
    return c


async def test_code_name_pair_names_code_column(tmp_path) -> None:
    conn = _conn(
        str(tmp_path / "a.duckdb"),
        [
            "CREATE TABLE clinical_info(icd_o_3_histology VARCHAR, histological_type VARCHAR)",
            "INSERT INTO clinical_info VALUES "
            "('9400/3','Astrocytoma'),('9401/3','Astrocytoma'),"
            "('9450/3','Oligodendroglioma'),('9382/3','Oligoastrocytoma')",
        ],
    )
    try:
        section = build_code_name_notes(await _profile(conn), conn)
    finally:
        conn.disconnect()
    assert section is not None
    assert section.heading == "Code Columns"
    assert section.source == "verified"
    assert "icd_o_3_histology" in section.body  # the code column is the grouping key
    assert "histological_type" in section.body  # named as the display label


async def test_name_only_table_emits_nothing(tmp_path) -> None:
    conn = _conn(
        str(tmp_path / "b.duckdb"),
        [
            "CREATE TABLE city(name VARCHAR)",
            "INSERT INTO city VALUES ('London'),('Paris'),('Berlin')",
        ],
    )
    try:
        section = build_code_name_notes(await _profile(conn), conn)
    finally:
        conn.disconnect()
    assert section is None


async def test_two_code_shaped_columns_are_ambiguous_and_dropped(tmp_path) -> None:
    # both columns are code-shaped -> neither qualifies as the display-name column -> drop
    conn = _conn(
        str(tmp_path / "c.duckdb"),
        [
            "CREATE TABLE pair(a VARCHAR, b VARCHAR)",
            "INSERT INTO pair VALUES ('9400/3','X12'),('9401/3','X13'),('9450/3','X14')",
        ],
    )
    try:
        section = build_code_name_notes(await _profile(conn), conn)
    finally:
        conn.disconnect()
    assert section is None
