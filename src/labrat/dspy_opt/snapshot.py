"""Reference snapshot: capture pre-run DuckDB state for post-build verification (Phase 3).

Captures schema, row count, and sample rows from tables that exist in the task's DuckDB
before the agent runs. This gives the verifier ground truth to compare against after dbt run.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb


@dataclass
class TableSnapshot:
    table_name: str
    column_names: list[str]
    column_types: list[str]
    row_count: int | None
    sample_rows: list[tuple[Any, ...]] = field(default_factory=list)


@dataclass
class ReferenceSnapshot:
    """Snapshot of DuckDB table state captured before the agent runs."""

    tables: dict[str, TableSnapshot] = field(default_factory=dict)


def capture_reference_snapshot(
    db_path: Path,
    table_names: list[str] | None = None,
    sample_size: int = 3,
) -> ReferenceSnapshot:
    """Capture schema, row count, and sample rows from live DuckDB tables.

    Args:
        db_path:      path to the DuckDB file
        table_names:  if given, only snapshot these tables; otherwise all tables
        sample_size:  rows to capture per table

    Returns:
        ReferenceSnapshot — always non-None, but may have empty tables dict if DB
        doesn't exist or is empty.
    """
    snapshot = ReferenceSnapshot()

    if not db_path.exists():
        return snapshot

    try:
        conn = duckdb.connect(str(db_path), read_only=True)
    except Exception:
        return snapshot

    try:
        existing = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}

        targets = table_names if table_names else list(existing)

        for tname in targets:
            if tname not in existing:
                continue
            try:
                snap = _snapshot_table(conn, tname, sample_size)
                if snap is not None:
                    snapshot.tables[tname] = snap
            except Exception:
                continue
    finally:
        conn.close()

    return snapshot


def _snapshot_table(conn: duckdb.DuckDBPyConnection, table_name: str, sample_size: int) -> TableSnapshot | None:
    desc = conn.execute(f"DESCRIBE {table_name}").fetchall()  # noqa: S608
    if not desc:
        return None

    col_names = [row[0] for row in desc]
    col_types = [row[1] for row in desc]

    try:
        row_count_res = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()  # noqa: S608
        row_count = int(row_count_res[0]) if row_count_res else None
    except Exception:
        row_count = None

    try:
        sample_rel = conn.execute(f"SELECT * FROM {table_name} LIMIT {sample_size}")  # noqa: S608
        sample_rows = [tuple(r) for r in sample_rel.fetchall()]
    except Exception:
        sample_rows = []

    return TableSnapshot(
        table_name=table_name,
        column_names=col_names,
        column_types=col_types,
        row_count=row_count,
        sample_rows=sample_rows,
    )


def format_snapshot_markdown(snapshot: ReferenceSnapshot) -> str:
    """Render snapshot as markdown for injection into the agent context."""
    if not snapshot.tables:
        return "No pre-existing tables found in DuckDB."

    lines: list[str] = ["## Reference Snapshot (pre-run DuckDB state)", ""]
    for tname, snap in snapshot.tables.items():
        pre = "yes" if snap.row_count and snap.row_count > 0 else "no"
        lines.append(f"### {tname} (pre-existing: {pre})")
        if snap.row_count is not None:
            lines.append(f"Row count: {snap.row_count:,}")
        col_desc = ", ".join(f"{c} {t}" for c, t in zip(snap.column_names, snap.column_types))
        lines.append(f"Columns: {col_desc}")
        if snap.sample_rows:
            header = " | ".join(snap.column_names)
            sep = " | ".join("---" for _ in snap.column_names)
            lines += ["Sample rows:", f"| {header} |", f"| {sep} |"]
            for row in snap.sample_rows:
                lines.append("| " + " | ".join(_fmt(v) for v in row) + " |")
        lines.append("")

    return "\n".join(lines).rstrip()


def _fmt(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, float) and math.isnan(v):
        return "NaN"
    return str(v)
