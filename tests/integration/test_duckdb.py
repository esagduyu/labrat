"""Integration tests for DuckDB connection against the fixture database."""

from pathlib import Path

import pytest

from labrat.db.duckdb_engine import DuckDBConnection

FIXTURE_DB = Path(__file__).parent.parent / "fixtures" / "sample_dbs" / "test.duckdb"


@pytest.fixture()
def conn() -> DuckDBConnection:
    db = DuckDBConnection(FIXTURE_DB, read_only=True)
    db.connect()
    yield db  # type: ignore[misc]
    db.disconnect()


def test_introspect_returns_catalog(conn: DuckDBConnection) -> None:
    catalog = conn.introspect_catalog()
    table_names = {t.name for s in catalog.schemas for t in s.tables}
    assert {"orders", "users", "regions"}.issubset(table_names)


def test_introspect_orders_columns(conn: DuckDBConnection) -> None:
    catalog = conn.introspect_catalog()
    orders = catalog.find_table("orders")
    assert orders is not None
    col_names = {c.name for c in orders.columns}
    assert {"id", "user_id", "region_id", "total_amount", "status", "order_date"}.issubset(
        col_names
    )


def test_execute_returns_dataframe(conn: DuckDBConnection) -> None:
    df = conn.execute("SELECT * FROM orders LIMIT 5")
    assert len(df) == 5
    assert "total_amount" in df.columns


def test_explain_returns_string(conn: DuckDBConnection) -> None:
    plan = conn.explain("SELECT * FROM orders LIMIT 5")
    assert isinstance(plan, str)
    assert len(plan) > 0


def test_sample_table_returns_n_rows(conn: DuckDBConnection) -> None:
    df = conn.sample_table("orders", n=3)
    assert len(df) == 3


def test_column_stats_total_amount(conn: DuckDBConnection) -> None:
    stats = conn.column_stats("orders", "total_amount")
    assert stats.column_name == "total_amount"
    assert stats.null_count == 0
    assert stats.distinct_count > 0
    assert stats.min_value is not None
    assert stats.max_value is not None


def test_connection_context_manager() -> None:
    with DuckDBConnection(FIXTURE_DB, read_only=True) as conn:
        df = conn.execute("SELECT 1 AS val")
        assert df["val"][0] == 1


def test_disconnect_clears_connection() -> None:
    db = DuckDBConnection(":memory:", read_only=False)
    db.connect()
    db.disconnect()
    with pytest.raises(RuntimeError, match="Not connected"):
        db.execute("SELECT 1")
