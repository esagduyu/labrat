"""Value-range + stratified format-sampling in Dimensions (M0 Deterministic Data-
Intelligence Pack, task 4)."""

from __future__ import annotations

import duckdb

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.profile_dataset import ProfileDatasetTool
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import build_dimensions


async def test_ranges_and_format_samples(tmp_path) -> None:
    p = str(tmp_path / "v.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE t(n INT, path VARCHAR)")
    raw.execute("INSERT INTO t VALUES (1,'a>b>c'),(50,'plain'),(999,'x::y::z')")
    raw.close()
    conn = DuckDBConnection(path=p)
    conn.connect()
    catalog = conn.introspect_catalog()
    prof = await ProfileDatasetTool().execute(
        ToolContext(connection=conn, catalog=catalog, primary="main"),
        ProfileDatasetTool().input_model(sample_rows=0, max_tables=100),
    )
    body = build_dimensions(prof, conn).body
    assert "1..999" in body or "min 1" in body  # numeric range for n
    assert "a>b>c" in body or "x::y::z" in body  # unusual-structure sample surfaced
    conn.disconnect()
