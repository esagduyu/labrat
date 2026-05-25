"""Tests for SQL execution tools: explain_sql and run_sql (M13)."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.agent.tools.explain_sql import ExplainSqlTool
from labrat.agent.tools.run_sql import RunSqlTool
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
def ctx(fixture_db: Path) -> ToolContext:  # type: ignore[misc]
    conn = DuckDBConnection(fixture_db, read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    yield ToolContext(connection=conn, catalog=catalog, profile_name="fixture")  # type: ignore[misc]
    conn.disconnect()


# ── explain_sql ───────────────────────────────────────────────────────────────


async def test_explain_sql_returns_plan(ctx: ToolContext) -> None:
    """explain_sql returns a non-empty plan string."""
    tool = ExplainSqlTool()
    result = await tool.execute(ctx, tool.input_model(query="SELECT * FROM orders LIMIT 5"))
    assert isinstance(result.plan, str)
    assert len(result.plan) > 0


async def test_explain_sql_includes_query(ctx: ToolContext) -> None:
    """explain_sql echoes the query in the output."""
    tool = ExplainSqlTool()
    sql = "SELECT COUNT(*) FROM orders"
    result = await tool.execute(ctx, tool.input_model(query=sql))
    assert result.query == sql


# ── run_sql: success paths ────────────────────────────────────────────────────


async def test_run_sql_select_returns_rows(ctx: ToolContext) -> None:
    """run_sql executes a SELECT and returns rows as a list of dicts."""
    tool = RunSqlTool()
    result = await tool.execute(ctx, tool.input_model(query="SELECT * FROM orders LIMIT 5"))
    assert result.ok is True
    assert result.row_count == 5
    assert result.columns is not None
    assert "total_amount" in result.columns


async def test_run_sql_auto_limit_applied(ctx: ToolContext) -> None:
    """run_sql injects LIMIT auto_limit when not already present."""
    tool = RunSqlTool()
    result = await tool.execute(ctx, tool.input_model(query="SELECT * FROM orders", auto_limit=3))
    assert result.ok is True
    assert result.row_count == 3


async def test_run_sql_existing_limit_respected(ctx: ToolContext) -> None:
    """run_sql does not double-limit when the query already has LIMIT."""
    tool = RunSqlTool()
    result = await tool.execute(
        ctx, tool.input_model(query="SELECT * FROM orders LIMIT 2", auto_limit=100)
    )
    assert result.ok is True
    assert result.row_count == 2


async def test_run_sql_count_star(ctx: ToolContext) -> None:
    """run_sql can execute aggregate queries."""
    tool = RunSqlTool()
    result = await tool.execute(ctx, tool.input_model(query="SELECT COUNT(*) AS n FROM orders"))
    assert result.ok is True
    assert result.row_count == 1


# ── run_sql: mutation refusal ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE orders",
        "DELETE FROM orders WHERE 1=1",
        "INSERT INTO orders VALUES (99, 1, 1, 10.0, 'new', '2025-01-01')",
        "UPDATE orders SET status = 'closed'",
        "TRUNCATE orders",
        "CREATE TABLE foo (id INT)",
        "ALTER TABLE orders ADD COLUMN foo INT",
    ],
)
async def test_run_sql_rejects_mutations(ctx: ToolContext, sql: str) -> None:
    """run_sql returns ok=False and a refusal message for any mutation statement."""
    tool = RunSqlTool()
    result = await tool.execute(ctx, tool.input_model(query=sql))
    assert result.ok is False
    assert result.refused is True
    assert result.error is not None


# ── run_sql: registered in registry ──────────────────────────────────────────


def test_both_tools_register_cleanly() -> None:
    """explain_sql and run_sql register and expose valid Anthropic schemas."""
    registry = ToolRegistry()
    registry.register(ExplainSqlTool())
    registry.register(RunSqlTool())
    names = {t.name for t in registry.tools}
    assert names == {"explain_sql", "run_sql"}
    for schema in registry.to_anthropic_schemas():
        assert "name" in schema
        assert "description" in schema
        assert schema["input_schema"]["type"] == "object"


# ── history log ───────────────────────────────────────────────────────────────


async def test_run_sql_logs_successful_query(
    ctx: ToolContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_sql appends a parseable JSONL entry on successful execution."""
    import labrat.agent.tools.run_sql as run_sql_mod
    from labrat.history.log import QueryHistoryLog

    monkeypatch.setattr(run_sql_mod, "_history_log", QueryHistoryLog(history_dir=tmp_path))
    tool = RunSqlTool()
    await tool.execute(ctx, tool.input_model(query="SELECT 1 AS val"))
    log_file = tmp_path / f"{ctx.profile_name}.jsonl"
    assert log_file.exists()
    entry = json.loads(log_file.read_text().strip())
    assert entry["executed"] is True
    assert entry["success"] is True
    assert entry["profile"] == "fixture"


async def test_run_sql_logs_refused_query(
    ctx: ToolContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_sql logs refused mutations with executed=False."""
    import labrat.agent.tools.run_sql as run_sql_mod
    from labrat.history.log import QueryHistoryLog

    monkeypatch.setattr(run_sql_mod, "_history_log", QueryHistoryLog(history_dir=tmp_path))
    tool = RunSqlTool()
    await tool.execute(ctx, tool.input_model(query="DROP TABLE orders"))
    log_file = tmp_path / f"{ctx.profile_name}.jsonl"
    assert log_file.exists()
    entry = json.loads(log_file.read_text().strip())
    assert entry["executed"] is False
    assert entry["success"] is False
