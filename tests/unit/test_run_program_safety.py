"""run_program safety wiring: recursion guard, read-only gate, dispatch validation."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

import labrat.agent.tools.run_sql as run_sql_mod
from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.program.interpreter import ProgramResult
from labrat.agent.tools.base import ToolContext
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.history.log import QueryHistoryLog


def _make_duckdb(tmp_path: Path) -> DuckDBConnection:
    path = str(tmp_path / "safety.duckdb")
    raw = duckdb.connect(path)
    raw.execute("CREATE TABLE t (id INTEGER)")
    raw.execute("INSERT INTO t VALUES (1)")
    raw.close()
    conn = DuckDBConnection(path=path, read_only=False)
    conn.connect()
    return conn


async def test_nested_run_program_step_is_unknown_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recursion guard: run_program is NOT in its own sub-registry."""
    monkeypatch.setattr(run_sql_mod, "_history_log", QueryHistoryLog(history_dir=tmp_path))
    conn = _make_duckdb(tmp_path)
    registry = build_data_tools_registry()
    ctx = ToolContext(connection=conn, catalog=None)
    result = await registry.dispatch(
        "run_program",
        {
            "steps": [
                {
                    "tool": "run_program",
                    "args": {
                        "steps": [{"tool": "run_sql", "args": {"query": "SELECT 1"}, "bind": "x"}]
                    },
                    "bind": "inner",
                }
            ]
        },
        ctx,
    )
    # The OUTER dispatch succeeds (the tool ran); the inner step fails cleanly.
    assert result.ok
    out = result.value
    assert isinstance(out, ProgramResult)
    assert not out.ok
    assert out.steps[0].ok is False
    assert out.steps[0].error is not None
    assert "Unknown tool" in out.steps[0].error
    conn.disconnect()


async def test_read_only_ctx_blocks_run_program_at_dispatch(tmp_path: Path) -> None:
    """mutating=True composes with the M3 read-only Analyst-mode gate."""
    conn = _make_duckdb(tmp_path)
    registry = build_data_tools_registry()
    ctx = ToolContext(connection=conn, catalog=None, read_only=True)
    result = await registry.dispatch(
        "run_program",
        {"steps": [{"tool": "list_tables", "args": {}, "bind": "t"}]},
        ctx,
    )
    assert not result.ok
    assert result.error == "blocked: read-only Analyst mode"
    conn.disconnect()


async def test_duplicate_bind_rejected_at_dispatch(tmp_path: Path) -> None:
    """Model validators fire during registry input validation — DispatchResult, not a crash."""
    conn = _make_duckdb(tmp_path)
    registry = build_data_tools_registry()
    ctx = ToolContext(connection=conn, catalog=None)
    result = await registry.dispatch(
        "run_program",
        {
            "steps": [
                {"tool": "run_sql", "args": {"query": "SELECT 1"}, "bind": "x"},
                {"tool": "run_sql", "args": {"query": "SELECT 2"}, "bind": "x"},
            ]
        },
        ctx,
    )
    assert not result.ok
    assert result.error is not None
    assert "duplicate bind" in result.error
    conn.disconnect()


async def test_unsafe_bind_rejected_at_dispatch(tmp_path: Path) -> None:
    conn = _make_duckdb(tmp_path)
    registry = build_data_tools_registry()
    ctx = ToolContext(connection=conn, catalog=None)
    result = await registry.dispatch(
        "run_program",
        {"steps": [{"tool": "run_sql", "args": {"query": "SELECT 1"}, "bind": "x; drop"}]},
        ctx,
    )
    assert not result.ok
    assert result.error is not None
    assert "letter/underscore" in result.error
    conn.disconnect()
