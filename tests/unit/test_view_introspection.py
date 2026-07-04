"""Unit B: Table.view_definition + DuckDB view introspection."""

from __future__ import annotations

from pathlib import Path  # noqa: F401 -- used by Task 5's DuckDB view-introspection tests

import duckdb  # noqa: F401 -- used by Task 5's DuckDB view-introspection tests

from labrat.db.catalog import Table
from labrat.db.duckdb_engine import DuckDBConnection  # noqa: F401 -- used by Task 5


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
