"""Wide-DB schema compaction for build_key_tables (M0 Deterministic Data-Intelligence Pack)."""

from __future__ import annotations

from labrat.agent.tools.profile_dataset import _ColumnInfo, _TableProfile
from labrat.agent.tools.profile_dataset import _Output as ProfileOutput
from labrat.maze.cartographer import build_key_tables


def _mk_profile() -> ProfileOutput:
    cols = [
        _ColumnInfo(name="d", data_type="DATE", nullable=True),
        _ColumnInfo(name="close", data_type="DOUBLE", nullable=True),
    ]
    tables = [
        _TableProfile(
            name=f"ticker_{i}",
            schema_name="main",
            row_count=100,
            columns=cols,
            foreign_keys=[],
        )
        for i in range(10)
    ]
    return ProfileOutput(database="d", tables_total=10, tables_profiled=10, tables=tables)


def test_identical_tables_compacted() -> None:
    body = build_key_tables(_mk_profile(), joins=[]).body
    assert "share this structure" in body.lower() or "10 tables" in body
    assert body.count("### ticker_") <= 1  # not 10 separate blocks
