"""Post-build deterministic verifier for Spider2-DBT (Phase 3).

Runs after `dbt run` returns 0. Checks column schema, row count, and value
spot-checks against the reference snapshot — pure Python, no LLM.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from labrat.dspy_opt.snapshot import ReferenceSnapshot

_ROW_COUNT_TOLERANCE = 0.10  # 10%


@dataclass
class ColumnMismatch:
    table: str
    col: str
    expected_type: str
    got_type: str


@dataclass
class RowCountMismatch:
    table: str
    snapshot_count: int
    built_count: int
    delta_pct: float


@dataclass
class ValueMismatch:
    table: str
    pk_col: str
    pk_val: Any
    col: str
    expected: Any
    got: Any | None  # None means the row was not found


@dataclass
class MissingTable:
    table: str


@dataclass
class VerifyResult:
    missing_tables: list[MissingTable] = field(default_factory=list)
    column_mismatches: list[ColumnMismatch] = field(default_factory=list)
    row_count_mismatches: list[RowCountMismatch] = field(default_factory=list)
    value_mismatches: list[ValueMismatch] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            not self.missing_tables
            and not self.column_mismatches
            and not self.row_count_mismatches
            and not self.value_mismatches
        )

    def format_report(self) -> str:
        if self.passed:
            return "Verifier: all checks passed."

        lines: list[str] = ["Verifier found issues — fix before submitting:", ""]

        for m in self.missing_tables:
            lines.append(f"- MISSING TABLE: '{m.table}' was not built by dbt run.")

        for cm in self.column_mismatches:
            lines.append(
                f"- COLUMN TYPE MISMATCH in {cm.table}.{cm.col}: "
                f"expected {cm.expected_type}, got {cm.got_type}"
            )

        for rcm in self.row_count_mismatches:
            lines.append(
                f"- ROW COUNT MISMATCH in {rcm.table}: "
                f"snapshot={rcm.snapshot_count:,}, built={rcm.built_count:,} "
                f"({rcm.delta_pct:+.1f}%)"
            )

        for vm in self.value_mismatches:
            if vm.got is None:
                lines.append(
                    f"- VALUE MISSING in {vm.table}: "
                    f"expected row where {vm.pk_col}={vm.pk_val!r} "
                    f"to have {vm.col}={vm.expected!r}, "
                    "but the row was not found."
                )
            else:
                lines.append(
                    f"- VALUE MISMATCH in {vm.table}.{vm.col} "
                    f"where {vm.pk_col}={vm.pk_val!r}: "
                    f"expected {vm.expected!r}, got {vm.got!r}"
                )

        return "\n".join(lines)


def verify_build(
    db_path: Path,
    eval_tables: list[str],
    snapshot: ReferenceSnapshot | None = None,
) -> VerifyResult:
    """Check that eval_tables were built correctly, comparing against snapshot.

    Args:
        db_path:     path to the built DuckDB file
        eval_tables: table names the benchmark will evaluate
        snapshot:    pre-run snapshot; if None, only existence and row-count>0 are checked

    Returns:
        VerifyResult dataclass — call .passed and .format_report() on it.
    """
    result = VerifyResult()

    if not db_path.exists():
        for t in eval_tables:
            result.missing_tables.append(MissingTable(table=t))
        return result

    try:
        conn = duckdb.connect(str(db_path), read_only=True)
    except Exception as exc:
        result.missing_tables.append(MissingTable(table=f"(could not open DB: {exc})"))
        return result

    try:
        existing = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}

        for tname in eval_tables:
            if tname not in existing:
                result.missing_tables.append(MissingTable(table=tname))
                continue

            _check_table(conn, tname, snapshot, result)
    finally:
        conn.close()

    return result


def _check_table(
    conn: duckdb.DuckDBPyConnection,
    tname: str,
    snapshot: ReferenceSnapshot | None,
    result: VerifyResult,
) -> None:
    # Get current schema
    try:
        desc = conn.execute(f"DESCRIBE {tname}").fetchall()
    except Exception:
        result.missing_tables.append(MissingTable(table=tname))
        return

    built_cols = {row[0].lower(): row[1] for row in desc}

    # Get current row count
    try:
        row_count_res = conn.execute(f"SELECT COUNT(*) FROM {tname}").fetchone()
        built_count = int(row_count_res[0]) if row_count_res else 0
    except Exception:
        built_count = 0

    snap_table = snapshot.tables.get(tname) if snapshot else None

    if snap_table:
        # Column type check: only flag if snap had types and built differs
        for col, snap_type in zip(snap_table.column_names, snap_table.column_types, strict=False):
            col_lower = col.lower()
            built_type = built_cols.get(col_lower)
            if built_type is None:
                continue  # column wasn't in snapshot but absent now — skip (could be new col)
            if not _types_compatible(snap_type, built_type):
                result.column_mismatches.append(
                    ColumnMismatch(
                        table=tname,
                        col=col,
                        expected_type=snap_type,
                        got_type=built_type,
                    )
                )

        # Row count check: compare against snapshot if it had data
        if snap_table.row_count and snap_table.row_count > 0:
            delta = (built_count - snap_table.row_count) / snap_table.row_count
            if abs(delta) > _ROW_COUNT_TOLERANCE:
                result.row_count_mismatches.append(
                    RowCountMismatch(
                        table=tname,
                        snapshot_count=snap_table.row_count,
                        built_count=built_count,
                        delta_pct=delta * 100,
                    )
                )

        # Value spot-check: verify snapshot sample rows exist in built table
        if snap_table.column_names and snap_table.sample_rows:
            pk_col = snap_table.column_names[0]
            for sample_row in snap_table.sample_rows[:2]:
                pk_val = sample_row[0]
                if pk_val is None:
                    continue
                _spot_check_row(
                    conn, tname, pk_col, pk_val, snap_table.column_names, sample_row, result
                )
    else:
        # No snapshot for this table — just verify non-empty
        if built_count == 0:
            result.row_count_mismatches.append(
                RowCountMismatch(
                    table=tname,
                    snapshot_count=0,
                    built_count=0,
                    delta_pct=0.0,
                )
            )


def _spot_check_row(
    conn: duckdb.DuckDBPyConnection,
    tname: str,
    pk_col: str,
    pk_val: Any,
    col_names: list[str],
    expected_row: tuple[Any, ...],
    result: VerifyResult,
) -> None:
    try:
        found = conn.execute(
            f"SELECT * FROM {tname} WHERE {pk_col} = ? LIMIT 1",
            [pk_val],
        ).fetchone()
    except Exception:
        return

    if found is None:
        result.value_mismatches.append(
            ValueMismatch(
                table=tname,
                pk_col=pk_col,
                pk_val=pk_val,
                col=col_names[0] if col_names else "?",
                expected=expected_row[0] if expected_row else None,
                got=None,
            )
        )
        return

    # Compare each column with tolerance for numerics
    for col, exp_val, got_val in zip(col_names, expected_row, found, strict=False):
        if exp_val is None and got_val is None:
            continue
        if exp_val is None or got_val is None:
            continue  # NULL mismatches are common and expected — skip
        if isinstance(exp_val, float) and math.isnan(exp_val):
            continue
        if isinstance(exp_val, (int, float)) and isinstance(got_val, (int, float)):
            if not math.isclose(float(exp_val), float(got_val), abs_tol=1e-2):
                result.value_mismatches.append(
                    ValueMismatch(
                        table=tname,
                        pk_col=pk_col,
                        pk_val=pk_val,
                        col=col,
                        expected=exp_val,
                        got=got_val,
                    )
                )
        elif str(exp_val) != str(got_val):
            result.value_mismatches.append(
                ValueMismatch(
                    table=tname,
                    pk_col=pk_col,
                    pk_val=pk_val,
                    col=col,
                    expected=exp_val,
                    got=got_val,
                )
            )


def _types_compatible(t1: str, t2: str) -> bool:
    """Loose type compatibility: INTEGER/BIGINT/INT are interchangeable, etc."""
    def _family(t: str) -> str:
        t = t.upper().split("(")[0].strip()
        if t in ("INTEGER", "INT", "INT4", "BIGINT", "INT8", "SMALLINT", "TINYINT", "HUGEINT"):
            return "INT"
        if t in ("FLOAT", "DOUBLE", "REAL", "DECIMAL", "NUMERIC", "FLOAT4", "FLOAT8"):
            return "FLOAT"
        if t in ("VARCHAR", "TEXT", "CHAR", "STRING", "BLOB"):
            return "TEXT"
        if t in ("BOOLEAN", "BOOL"):
            return "BOOL"
        if t in ("DATE",):
            return "DATE"
        if t in ("TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ"):
            return "TIMESTAMP"
        return t

    return _family(t1) == _family(t2)
