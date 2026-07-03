from __future__ import annotations

import duckdb

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.profile_dataset import ProfileDatasetTool
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import build_dimensions


async def _profile(tmp_path):
    p = str(tmp_path / "v.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE t(id INT, note VARCHAR)")
    raw.execute("INSERT INTO t SELECT i, 'note-' || i || '->x' FROM range(200) tbl(i)")
    raw.close()
    conn = DuckDBConnection(path=p, read_only=False)
    conn.connect()
    prof = await ProfileDatasetTool().execute(
        ToolContext(connection=conn, catalog=conn.introspect_catalog(), primary="main"),
        ProfileDatasetTool().input_model(sample_rows=0, max_tables=100),
    )
    return conn, prof


async def test_variant_seed_changes_high_card_samples(tmp_path) -> None:
    conn, prof = await _profile(tmp_path)
    b0 = build_dimensions(prof, conn, variant_seed=0).body
    b1 = build_dimensions(prof, conn, variant_seed=1).body
    assert b0 != b1  # different seeded example samples on the high-cardinality note column
    conn.disconnect()


async def test_variant_seed_zero_is_stable(tmp_path) -> None:
    conn, prof = await _profile(tmp_path)
    assert (
        build_dimensions(prof, conn, variant_seed=0).body
        == build_dimensions(prof, conn, variant_seed=0).body
    )
    conn.disconnect()
