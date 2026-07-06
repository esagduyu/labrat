"""Program-mode composition: run_sql -> llm_extract(stub) -> run_sql join, ONE call.

The M4 payoff test: three tool calls collapse into one run_program dispatch;
intermediate tables live as program_<bind> temp tables; only the bounded
summary returns — the row payloads NEVER appear in the tool result.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import duckdb
import pytest

import labrat.agent.tools.run_sql as run_sql_mod
from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.program.interpreter import ProgramResult
from labrat.agent.tools.base import ToolContext
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.history.log import QueryHistoryLog

_SENTINEL_1 = "sentinel_abstract_alpha"
_SENTINEL_2 = "sentinel_abstract_beta"


def _make_duckdb(tmp_path: Path) -> DuckDBConnection:
    path = str(tmp_path / "compose.duckdb")
    raw = duckdb.connect(path)
    raw.execute("CREATE TABLE patents (id INTEGER, abstract VARCHAR)")
    raw.execute(
        "INSERT INTO patents VALUES "
        f"(1, '{_SENTINEL_1} mentions aspirin'), (2, '{_SENTINEL_2} mentions ibuprofen')"
    )
    raw.close()
    conn = DuckDBConnection(path=path, read_only=False)
    conn.connect()
    return conn


async def _fake_llm(prompt: str) -> str:
    if "aspirin" in prompt:
        return json.dumps({"drug": "aspirin"})
    return json.dumps({"drug": "ibuprofen"})


_PROGRAM_ARGS: dict[str, Any] = {
    "steps": [
        {
            "tool": "run_sql",
            "args": {"query": "SELECT id, abstract FROM patents"},
            "bind": "docs",
        },
        {
            "tool": "llm_extract",
            "args": {
                "table": "$docs",
                "text_column": "abstract",
                "json_schema": {"properties": {"drug": {"type": "string"}}},
                "key_columns": ["id"],
            },
            "bind": "facts",
        },
        {
            "tool": "run_sql",
            "args": {
                "query": "SELECT d.id, f.drug FROM $facts f JOIN $docs d USING (id) ORDER BY d.id"
            },
            "bind": "final",
        },
    ]
}


async def test_three_step_composition_single_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_sql_mod, "_history_log", QueryHistoryLog(history_dir=tmp_path))
    conn = _make_duckdb(tmp_path)
    registry = build_data_tools_registry()
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_llm)

    result = await registry.dispatch("run_program", _PROGRAM_ARGS, ctx)
    assert result.ok
    out = result.value
    assert isinstance(out, ProgramResult)
    assert out.ok
    assert [s.ok for s in out.steps] == [True, True, True]

    # Handle temp tables exist and were what the join read.
    assert conn.execute("SELECT COUNT(*) AS n FROM program_docs")["n"].to_list() == [2]
    assert conn.execute("SELECT COUNT(*) AS n FROM program_facts")["n"].to_list() == [2]

    # Per-step summaries are populated (rows from row_count / rows_processed).
    assert out.steps[0].handle_table == "program_docs"
    assert out.steps[0].rows == 2
    assert out.steps[1].handle_table == "program_facts"
    assert out.steps[1].rows == 2
    assert out.steps[1].rows_failed == 0
    assert out.final_bind == "final"
    assert out.final_table == "program_final"

    # Bounded: NO intermediate row data in the returned summary.
    dumped = out.model_dump_json()
    assert _SENTINEL_1 not in dumped
    assert _SENTINEL_2 not in dumped
    assert "aspirin" not in dumped

    # The model's follow-up: read the final handle with a plain run_sql.
    follow = await registry.dispatch(
        "run_sql", {"query": "SELECT drug FROM program_final ORDER BY id"}, ctx
    )
    assert follow.ok
    dump = cast(dict[str, Any], follow.value.model_dump())  # type: ignore[union-attr]
    assert dump["rows"] == [["aspirin"], ["ibuprofen"]]
    conn.disconnect()


async def test_composition_without_llm_fn_stops_at_extract_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """llm_extract self-errors (ok=False) without llm_fn — the program stops there."""
    monkeypatch.setattr(run_sql_mod, "_history_log", QueryHistoryLog(history_dir=tmp_path))
    conn = _make_duckdb(tmp_path)
    registry = build_data_tools_registry()
    ctx = ToolContext(connection=conn, catalog=None)  # llm_fn defaults None

    result = await registry.dispatch("run_program", _PROGRAM_ARGS, ctx)
    assert result.ok
    out = result.value
    assert isinstance(out, ProgramResult)
    assert not out.ok
    assert len(out.steps) == 2  # step 3 never dispatched
    assert out.steps[1].ok is False
    assert out.steps[1].error is not None
    assert "LLM-enabled context" in out.steps[1].error
    assert out.final_bind == "docs"  # last OK step
    conn.disconnect()
