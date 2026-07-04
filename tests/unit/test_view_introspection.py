"""Unit B: Table.view_definition + DuckDB view introspection."""

from __future__ import annotations

from pathlib import Path

import duckdb

from labrat.db.catalog import Table
from labrat.db.duckdb_engine import DuckDBConnection


def test_table_view_definition_defaults_none() -> None:
    t = Table(name="orders", schema_name="main", columns=[])
    assert t.view_definition is None


def test_table_view_definition_settable() -> None:
    t = Table(
        name="v",
        schema_name="main",
        columns=[],
        view_definition="CREATE VIEW v AS SELECT 1;",
    )
    assert t.view_definition == "CREATE VIEW v AS SELECT 1;"


def _view_db(tmp_path: Path) -> DuckDBConnection:
    p = str(tmp_path / "v.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE orders(id INTEGER, customer_id INTEGER, amount DOUBLE)")
    raw.execute("CREATE TABLE customers(id INTEGER, name VARCHAR)")
    raw.execute(
        "CREATE VIEW customer_spend AS "
        "SELECT c.name AS customer_name, SUM(o.amount) AS total "
        "FROM orders o JOIN customers c ON o.customer_id = c.id GROUP BY c.name"
    )
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    return conn


def test_view_enters_catalog_with_definition_and_columns(tmp_path: Path) -> None:
    conn = _view_db(tmp_path)
    try:
        cat = conn.introspect_catalog()
    finally:
        conn.disconnect()
    v = cat.find_table("customer_spend")
    assert v is not None
    assert v.view_definition is not None
    assert v.view_definition.upper().startswith("CREATE VIEW")
    assert [c.name for c in v.columns] == ["customer_name", "total"]
    assert [c.data_type for c in v.columns] == ["VARCHAR", "DOUBLE"]


def test_base_tables_keep_view_definition_none(tmp_path: Path) -> None:
    conn = _view_db(tmp_path)
    try:
        cat = conn.introspect_catalog()
    finally:
        conn.disconnect()
    for name in ("orders", "customers"):
        t = cat.find_table(name)
        assert t is not None
        assert t.view_definition is None


def test_no_views_db_surfaces_only_base_tables(tmp_path: Path) -> None:
    p = str(tmp_path / "plain.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE city(id INTEGER, name VARCHAR)")
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    try:
        cat = conn.introspect_catalog()
    finally:
        conn.disconnect()
    main = next(s for s in cat.schemas if s.name == "main")
    assert [t.name for t in main.tables] == ["city"]
    assert all(t.view_definition is None for t in main.tables)
