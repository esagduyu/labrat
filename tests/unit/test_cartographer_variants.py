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
    # Pin single-threaded execution: DuckDB's DISTINCT ... LIMIT without an ORDER BY
    # (the legacy seed-0 SQL this suite is pinning) has no guaranteed row order under
    # parallel hash aggregation, which made cross-call stability assertions flaky.
    # This only affects test determinism, not the production SQL under test.
    conn._connection.execute("PRAGMA threads=1")
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


class _RecordingConnProxy:
    """Wraps a Connection and records every SQL string passed to execute()."""

    def __init__(self, inner):
        self._inner = inner
        self.queries: list[str] = []

    def execute(self, sql: str):
        self.queries.append(sql)
        return self._inner.execute(sql)

    def __getattr__(self, name):
        return getattr(self._inner, name)


async def test_variant_seed_zero_emits_legacy_sql_no_order_by(tmp_path) -> None:
    """FIX 1 regression: seed=0 must reproduce the pre-M1 SQL byte-for-byte —
    no ORDER BY hash(...) clause on either the low-cardinality DISTINCT scan or
    the unusual-structure LIMIT-200 scan. The ORDER BY is only for variant_seed > 0.
    """
    conn, prof = await _profile(tmp_path)
    proxy = _RecordingConnProxy(conn)
    build_dimensions(prof, proxy, variant_seed=0)
    assert proxy.queries, "expected at least one query to be recorded"
    for sql in proxy.queries:
        assert "ORDER BY" not in sql, f"seed=0 query unexpectedly ordered: {sql!r}"
    conn.disconnect()


async def test_variant_seed_nonzero_emits_order_by(tmp_path) -> None:
    """Sanity check: variant_seed > 0 still adds the ORDER BY hash(...) clause
    that decorrelates consensus sub-runs' Scent."""
    conn, prof = await _profile(tmp_path)
    proxy = _RecordingConnProxy(conn)
    build_dimensions(prof, proxy, variant_seed=1)
    assert proxy.queries, "expected at least one query to be recorded"
    assert any("ORDER BY hash" in sql for sql in proxy.queries)
    conn.disconnect()
