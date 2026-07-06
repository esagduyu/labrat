"""RunProgramTool: registration, sub-registry-minus-self, e2e dispatch, schema."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import duckdb
import pytest

import labrat.agent.tools.run_sql as run_sql_mod
from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.program.interpreter import ProgramResult
from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.run_program import RunProgramTool
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.history.log import QueryHistoryLog


def _make_duckdb(tmp_path: Path) -> DuckDBConnection:
    path = str(tmp_path / "tool.duckdb")
    raw = duckdb.connect(path)
    raw.execute("CREATE TABLE patents (id INTEGER, abstract VARCHAR)")
    raw.execute("INSERT INTO patents VALUES (1, 'about aspirin'), (2, 'about ibuprofen')")
    raw.close()
    conn = DuckDBConnection(path=path, read_only=False)
    conn.connect()
    return conn


def test_run_program_registered_by_default() -> None:
    names = {t.name for t in build_data_tools_registry().tools}
    assert "run_program" in names


def test_sub_registry_excludes_run_program() -> None:
    names = {t.name for t in build_data_tools_registry(include_program=False).tools}
    assert "run_program" not in names
    assert "run_sql" in names  # everything else still there


def test_run_program_is_mutating() -> None:
    assert RunProgramTool.mutating is True


async def test_dispatch_one_step_program_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_sql_mod, "_history_log", QueryHistoryLog(history_dir=tmp_path))
    conn = _make_duckdb(tmp_path)
    registry = build_data_tools_registry()
    ctx = ToolContext(connection=conn, catalog=None)
    result = await registry.dispatch(
        "run_program",
        {
            "steps": [
                {
                    "tool": "run_sql",
                    "args": {"query": "SELECT id, abstract FROM patents"},
                    "bind": "docs",
                }
            ]
        },
        ctx,
    )
    assert result.ok
    out = result.value
    assert isinstance(out, ProgramResult)
    assert out.ok
    assert out.final_bind == "docs"
    assert out.final_table == "program_docs"
    # The final table is queryable by a follow-up run_sql on the SAME registry.
    follow = await registry.dispatch(
        "run_sql", {"query": "SELECT COUNT(*) AS n FROM program_docs"}, ctx
    )
    assert follow.ok
    dump = cast(dict[str, Any], follow.value.model_dump())  # type: ignore[union-attr]
    assert dump["rows"] == [["2"]]
    conn.disconnect()


def test_anthropic_schema_includes_program_step_defs() -> None:
    schema = RunProgramTool().anthropic_schema()
    defs = schema["input_schema"].get("$defs", {})
    assert "ProgramStep" in defs


def test_openai_schema_includes_program_step_defs() -> None:
    schema = RunProgramTool().openai_schema()
    defs = schema["function"]["parameters"].get("$defs", {})
    assert "ProgramStep" in defs
