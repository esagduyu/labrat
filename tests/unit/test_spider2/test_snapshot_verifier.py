"""Unit tests for reference snapshot and deterministic verifier (Phase 3)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from labrat.dspy_opt.snapshot import (
    ReferenceSnapshot,
    TableSnapshot,
    capture_reference_snapshot,
    format_snapshot_markdown,
)
from labrat.dspy_opt.verifier import (
    VerifyResult,
    _types_compatible,
    verify_build,
)

# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def duck_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        "CREATE TABLE orders (order_id INTEGER, customer_id INTEGER, status VARCHAR, amount DOUBLE)"
    )
    conn.execute(
        "INSERT INTO orders VALUES "
        "(1, 10, 'shipped', 99.5), (2, 11, 'pending', 50.0), (3, 10, 'delivered', 200.0)"
    )
    conn.close()
    return db_path


# ── snapshot tests ────────────────────────────────────────────────────────────


def test_capture_snapshot_basic(duck_db: Path) -> None:
    snap = capture_reference_snapshot(duck_db)
    assert "orders" in snap.tables
    s = snap.tables["orders"]
    assert "order_id" in s.column_names
    assert s.row_count == 3


def test_capture_snapshot_filtered_tables(duck_db: Path) -> None:
    snap = capture_reference_snapshot(duck_db, table_names=["orders"])
    assert "orders" in snap.tables


def test_capture_snapshot_missing_table_skipped(duck_db: Path) -> None:
    snap = capture_reference_snapshot(duck_db, table_names=["nonexistent"])
    assert "nonexistent" not in snap.tables


def test_capture_snapshot_nonexistent_db(tmp_path: Path) -> None:
    snap = capture_reference_snapshot(tmp_path / "ghost.duckdb")
    assert snap.tables == {}


def test_capture_snapshot_sample_rows(duck_db: Path) -> None:
    snap = capture_reference_snapshot(duck_db, sample_size=2)
    s = snap.tables["orders"]
    assert len(s.sample_rows) <= 2
    assert len(s.sample_rows) > 0


def test_format_snapshot_markdown_empty() -> None:
    snap = ReferenceSnapshot()
    text = format_snapshot_markdown(snap)
    assert "No pre-existing tables" in text


def test_format_snapshot_markdown_with_table(duck_db: Path) -> None:
    snap = capture_reference_snapshot(duck_db)
    text = format_snapshot_markdown(snap)
    assert "### orders" in text
    assert "Row count: 3" in text
    assert "order_id" in text


# ── verifier tests ────────────────────────────────────────────────────────────


def test_verify_build_passes_when_tables_exist(duck_db: Path) -> None:
    result = verify_build(duck_db, ["orders"])
    # No missing tables; row count > 0
    assert not result.missing_tables


def test_verify_build_reports_missing_table(duck_db: Path) -> None:
    result = verify_build(duck_db, ["orders", "nonexistent_table"])
    missing = [m.table for m in result.missing_tables]
    assert "nonexistent_table" in missing


def test_verify_build_nonexistent_db(tmp_path: Path) -> None:
    result = verify_build(tmp_path / "ghost.duckdb", ["orders"])
    assert len(result.missing_tables) > 0


def test_verify_build_row_count_mismatch(duck_db: Path) -> None:
    # Snapshot says 1000 rows, built has 3 — >10% difference
    snap = ReferenceSnapshot(
        tables={
            "orders": TableSnapshot(
                table_name="orders",
                column_names=["order_id"],
                column_types=["INTEGER"],
                row_count=1000,
                sample_rows=[],
            )
        }
    )
    result = verify_build(duck_db, ["orders"], snapshot=snap)
    assert len(result.row_count_mismatches) == 1
    assert result.row_count_mismatches[0].snapshot_count == 1000


def test_verify_build_row_count_ok_within_tolerance(duck_db: Path) -> None:
    # Snapshot says 3 rows, built has 3 — exactly the same
    snap = ReferenceSnapshot(
        tables={
            "orders": TableSnapshot(
                table_name="orders",
                column_names=["order_id"],
                column_types=["INTEGER"],
                row_count=3,
                sample_rows=[],
            )
        }
    )
    result = verify_build(duck_db, ["orders"], snapshot=snap)
    assert not result.row_count_mismatches


def test_verify_build_value_spot_check_pass(duck_db: Path) -> None:
    snap = ReferenceSnapshot(
        tables={
            "orders": TableSnapshot(
                table_name="orders",
                column_names=["order_id", "customer_id", "status", "amount"],
                column_types=["INTEGER", "INTEGER", "VARCHAR", "DOUBLE"],
                row_count=3,
                sample_rows=[(1, 10, "shipped", 99.5)],
            )
        }
    )
    result = verify_build(duck_db, ["orders"], snapshot=snap)
    # order_id=1 with those values exists in the DB — should pass
    assert not result.value_mismatches


def test_verify_build_zero_rows_flagged(duck_db: Path) -> None:
    # Create a table with no rows
    conn = duckdb.connect(str(duck_db))
    conn.execute("CREATE TABLE empty_model (id INTEGER)")
    conn.close()

    result = verify_build(duck_db, ["empty_model"])
    # No snapshot provided → should flag zero rows
    assert any(m.table == "empty_model" for m in result.row_count_mismatches)


def test_verify_result_passed_property() -> None:
    r = VerifyResult()
    assert r.passed is True


def test_verify_result_format_report_passed() -> None:
    r = VerifyResult()
    assert "passed" in r.format_report().lower()


def test_verify_result_format_report_failures() -> None:
    from labrat.dspy_opt.verifier import MissingTable

    r = VerifyResult(missing_tables=[MissingTable(table="my_model")])
    report = r.format_report()
    assert "my_model" in report
    assert "MISSING" in report


# ── type compatibility ────────────────────────────────────────────────────────


def test_types_compatible_int_variants() -> None:
    assert _types_compatible("INTEGER", "BIGINT")
    assert _types_compatible("INT4", "INT8")
    assert _types_compatible("SMALLINT", "HUGEINT")


def test_types_compatible_text_variants() -> None:
    assert _types_compatible("VARCHAR", "TEXT")
    assert _types_compatible("CHAR", "STRING")


def test_types_compatible_bool() -> None:
    assert _types_compatible("BOOLEAN", "BOOL")


def test_types_compatible_timestamp_variants() -> None:
    assert _types_compatible("TIMESTAMP", "TIMESTAMPTZ")


def test_types_incompatible_int_vs_text() -> None:
    assert not _types_compatible("INTEGER", "VARCHAR")


def test_types_incompatible_date_vs_timestamp() -> None:
    assert not _types_compatible("DATE", "TIMESTAMP")
