"""run_sql repair-oriented error diagnostics (FEATURE_ROADMAP #30)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from labrat.agent.tools import run_sql as run_sql_mod
from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.run_sql import RunSqlTool, _classify_sql_error
from labrat.db.duckdb_engine import DuckDBConnection


def test_classify_missing_column() -> None:
    cat, hint = _classify_sql_error(
        'Binder Error: Referenced column "foo" not found in FROM clause'
    )
    assert cat == "missing_column"
    assert hint


def test_classify_unknown_table() -> None:
    cat, hint = _classify_sql_error('Catalog Error: Table with name "bar" does not exist')
    assert cat == "unknown_table"
    assert hint


def test_classify_syntax() -> None:
    cat, _ = _classify_sql_error("Parser Error: syntax error at or near SELEC")
    assert cat == "syntax"


def test_classify_type_mismatch() -> None:
    cat, _ = _classify_sql_error("Conversion Error: Could not convert string to INTEGER")
    assert cat == "type_mismatch"


def test_classify_other_fallback() -> None:
    cat, hint = _classify_sql_error("some unexpected backend failure")
    assert cat == "other"
    assert hint


@pytest.fixture()
def ctx(ecommerce_db: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ToolContext]:
    monkeypatch.setattr(
        run_sql_mod._history_log, "append", lambda event: None
    )  # no log side effects
    conn = DuckDBConnection(ecommerce_db, read_only=True)
    conn.connect()
    yield ToolContext(connection=conn, catalog=conn.introspect_catalog())
    conn.disconnect()


async def test_bad_query_returns_diagnostics(ctx: ToolContext) -> None:
    tool = RunSqlTool()
    out = await tool.execute(ctx, tool.input_model(query="SELECT nonexistent_col FROM customers"))
    assert out.ok is False
    assert out.error_category == "missing_column"
    assert out.hint
    assert out.executed_sql and "customers" in out.executed_sql


async def test_valid_query_has_no_diagnostics(ctx: ToolContext) -> None:
    tool = RunSqlTool()
    out = await tool.execute(ctx, tool.input_model(query="SELECT customer_id FROM customers"))
    assert out.ok is True
    assert out.error_category is None
    assert out.hint is None
