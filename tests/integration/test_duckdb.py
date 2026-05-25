"""Integration tests for DuckDB connection against a generated fixture database."""

from pathlib import Path

import duckdb
import pytest

from labrat.db.duckdb_engine import DuckDBConnection


@pytest.fixture(scope="session")
def fixture_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    p = tmp_path_factory.mktemp("sample_dbs") / "test.duckdb"
    con = duckdb.connect(str(p))
    con.execute("""
        CREATE TABLE users (id INTEGER, name VARCHAR);
        INSERT INTO users VALUES (1,'Alice'),(2,'Bob'),(3,'Carol'),(4,'Dan'),(5,'Eve');
        CREATE TABLE regions (id INTEGER, name VARCHAR);
        INSERT INTO regions VALUES (1,'North'),(2,'South');
        CREATE TABLE orders (
            id INTEGER, user_id INTEGER, region_id INTEGER,
            total_amount DOUBLE, status VARCHAR, order_date DATE
        );
        INSERT INTO orders VALUES
            (1,1,1,100.0,'complete','2024-01-01'),
            (2,2,1,200.0,'pending','2024-01-02'),
            (3,3,2,150.0,'complete','2024-01-03'),
            (4,4,2,300.0,'cancelled','2024-01-04'),
            (5,5,1,50.0,'complete','2024-01-05');
    """)
    con.close()
    return p


@pytest.fixture()
def conn(fixture_db: Path) -> DuckDBConnection:
    db = DuckDBConnection(fixture_db, read_only=True)
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


def test_connection_context_manager(fixture_db: Path) -> None:
    with DuckDBConnection(fixture_db, read_only=True) as conn:
        df = conn.execute("SELECT 1 AS val")
        assert df["val"][0] == 1


def test_disconnect_clears_connection() -> None:
    db = DuckDBConnection(":memory:", read_only=False)
    db.connect()
    db.disconnect()
    with pytest.raises(RuntimeError, match="Not connected"):
        db.execute("SELECT 1")
