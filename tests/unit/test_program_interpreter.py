"""Program interpreter: sequential dispatch, handles, stop-on-error, max-steps."""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl
import pytest
from pydantic import BaseModel, PrivateAttr

import labrat.agent.tools.run_sql as run_sql_mod
from labrat.agent.program.dsl import Program, ProgramStep
from labrat.agent.program.interpreter import (
    DEFAULT_MAX_STEPS,
    ProgramResult,
    StepSummary,
    run_program,
)
from labrat.agent.tools.base import Tool, ToolContext, ToolRegistry
from labrat.agent.tools.run_sql import RunSqlTool
from labrat.agent.tools.serialization import LedgerPayloadKind
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.history.log import QueryHistoryLog

# ── stub tools (no DB, no LLM) ──────────────────────────────────────────────


class _EchoInput(BaseModel):
    text: str = ""


class _EchoOutput(BaseModel):
    ok: bool = True
    echoed: str = ""


class _EchoTool(Tool[_EchoInput]):
    """Records every call so tests can assert which steps actually dispatched."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo the input text."

    @property
    def input_model(self) -> type[_EchoInput]:
        return _EchoInput

    async def execute(self, ctx: ToolContext, args: _EchoInput) -> _EchoOutput:
        self.calls.append(args.text)
        return _EchoOutput(ok=True, echoed=args.text)


def _echo_registry() -> tuple[ToolRegistry, _EchoTool]:
    registry = ToolRegistry()
    echo = _EchoTool()
    registry.register(echo)
    return registry, echo


def _ctx() -> ToolContext:
    return ToolContext(connections={}, catalogs={})


# ── happy path ──────────────────────────────────────────────────────────────


async def test_two_step_program_runs_sequentially() -> None:
    registry, echo = _echo_registry()
    program = Program(
        steps=[
            ProgramStep(tool="echo", args={"text": "one"}, bind="a"),
            ProgramStep(tool="echo", args={"text": "two"}, bind="b"),
        ]
    )
    result = await run_program(program, _ctx(), registry)
    assert isinstance(result, ProgramResult)
    assert result.ok
    assert echo.calls == ["one", "two"]
    assert [s.bind for s in result.steps] == ["a", "b"]
    assert all(isinstance(s, StepSummary) and s.ok for s in result.steps)
    assert [s.index for s in result.steps] == [0, 1]
    assert result.final_bind == "b"
    assert result.final_table is None  # echo produces no table
    assert result.error is None


async def test_field_ref_passes_scalar_between_steps() -> None:
    registry, echo = _echo_registry()
    program = Program(
        steps=[
            ProgramStep(tool="echo", args={"text": "hello"}, bind="a"),
            ProgramStep(tool="echo", args={"text": "$a.echoed"}, bind="b"),
        ]
    )
    result = await run_program(program, _ctx(), registry)
    assert result.ok
    assert echo.calls == ["hello", "hello"]


# ── max-steps cap ───────────────────────────────────────────────────────────


async def test_default_max_steps_is_twenty() -> None:
    assert DEFAULT_MAX_STEPS == 20


async def test_over_max_steps_is_structured_error_and_nothing_runs() -> None:
    registry, echo = _echo_registry()
    program = Program(
        steps=[ProgramStep(tool="echo", args={"text": str(i)}, bind=f"s{i}") for i in range(3)]
    )
    result = await run_program(program, _ctx(), registry, max_steps=2)
    assert not result.ok
    assert result.error is not None
    assert "max_steps" in result.error
    assert result.steps == []  # nothing was run
    assert echo.calls == []


# ── stop-on-error ───────────────────────────────────────────────────────────


class _BoomInput(BaseModel):
    pass


class _BoomTool(Tool[_BoomInput]):
    """Raises inside execute — dispatch converts it to DispatchResult(ok=False)."""

    @property
    def name(self) -> str:
        return "boom"

    @property
    def description(self) -> str:
        return "Always raises."

    @property
    def input_model(self) -> type[_BoomInput]:
        return _BoomInput

    async def execute(self, ctx: ToolContext, args: _BoomInput) -> object:
        raise RuntimeError("boom exploded")


class _SoftFailOutput(BaseModel):
    ok: bool = False
    error: str | None = "soft failure: refused"


class _SoftFailTool(Tool[_BoomInput]):
    """Returns a structured ok=False output — dispatch itself SUCCEEDS."""

    @property
    def name(self) -> str:
        return "soft_fail"

    @property
    def description(self) -> str:
        return "Always returns ok=False without raising."

    @property
    def input_model(self) -> type[_BoomInput]:
        return _BoomInput

    async def execute(self, ctx: ToolContext, args: _BoomInput) -> _SoftFailOutput:
        return _SoftFailOutput()


async def test_raising_middle_step_stops_program() -> None:
    registry, echo = _echo_registry()
    registry.register(_BoomTool())
    program = Program(
        steps=[
            ProgramStep(tool="echo", args={"text": "one"}, bind="a"),
            ProgramStep(tool="boom", args={}, bind="b"),
            ProgramStep(tool="echo", args={"text": "never"}, bind="c"),
        ]
    )
    result = await run_program(program, _ctx(), registry)
    assert not result.ok
    assert echo.calls == ["one"]  # step 3 was NOT dispatched
    assert len(result.steps) == 2  # partial summary incl. the failing step
    assert result.steps[1].ok is False
    assert result.steps[1].error is not None
    assert "boom exploded" in result.steps[1].error
    assert result.error is not None
    assert "step 1" in result.error
    assert result.final_bind == "a"  # last OK step


async def test_soft_fail_output_stops_program() -> None:
    registry, echo = _echo_registry()
    registry.register(_SoftFailTool())
    program = Program(
        steps=[
            ProgramStep(tool="soft_fail", args={}, bind="a"),
            ProgramStep(tool="echo", args={"text": "never"}, bind="b"),
        ]
    )
    result = await run_program(program, _ctx(), registry)
    assert not result.ok
    assert echo.calls == []
    assert result.steps[0].ok is False
    assert result.steps[0].error is not None
    assert "soft failure" in result.steps[0].error


async def test_unknown_tool_stops_program() -> None:
    registry, echo = _echo_registry()
    program = Program(
        steps=[
            ProgramStep(tool="no_such_tool", args={}, bind="a"),
            ProgramStep(tool="echo", args={"text": "never"}, bind="b"),
        ]
    )
    result = await run_program(program, _ctx(), registry)
    assert not result.ok
    assert echo.calls == []
    assert result.steps[0].error is not None
    assert "Unknown tool" in result.steps[0].error


async def test_bad_ref_stops_program_as_failed_step() -> None:
    registry, echo = _echo_registry()
    program = Program(
        steps=[
            ProgramStep(tool="echo", args={"text": "one"}, bind="a"),
            ProgramStep(tool="echo", args={"text": "$missing.field"}, bind="b"),
        ]
    )
    result = await run_program(program, _ctx(), registry)
    assert not result.ok
    assert echo.calls == ["one"]  # step 2 never dispatched
    assert result.steps[1].ok is False
    assert result.steps[1].error is not None
    assert "unknown handle" in result.steps[1].error


# ── table materialization ───────────────────────────────────────────────────


class _TableOutput(BaseModel):
    ok: bool = True
    row_count: int = 2

    _df: pl.DataFrame | None = PrivateAttr(default=None)

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
        if self._df is not None:
            return ("table", self._df)
        return None

    def attach(self, df: pl.DataFrame) -> None:
        self._df = df


class _TableInput(BaseModel):
    pass


class _TableTool(Tool[_TableInput]):
    """Emits a 2-row table payload via the LedgerPayloadProvider hook."""

    @property
    def name(self) -> str:
        return "make_table"

    @property
    def description(self) -> str:
        return "Emit a fixed 2-row table."

    @property
    def input_model(self) -> type[_TableInput]:
        return _TableInput

    async def execute(self, ctx: ToolContext, args: _TableInput) -> _TableOutput:
        out = _TableOutput()
        out.attach(pl.DataFrame({"id": [1, 2], "v": ["sentinel_cell_a", "sentinel_cell_b"]}))
        return out


def _make_duckdb(tmp_path: Path) -> DuckDBConnection:
    path = str(tmp_path / "prog.duckdb")
    raw = duckdb.connect(path)
    raw.execute("CREATE TABLE patents (id INTEGER, abstract VARCHAR)")
    raw.execute("INSERT INTO patents VALUES (1, 'about aspirin'), (2, 'about ibuprofen')")
    raw.close()
    conn = DuckDBConnection(path=path, read_only=False)
    conn.connect()
    return conn


async def test_table_step_materializes_program_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_sql_mod, "_history_log", QueryHistoryLog(history_dir=tmp_path))
    conn = _make_duckdb(tmp_path)
    registry = ToolRegistry()
    registry.register(_TableTool())
    registry.register(RunSqlTool())
    ctx = ToolContext(connection=conn, catalog=None)
    program = Program(
        steps=[
            ProgramStep(tool="make_table", args={}, bind="docs"),
            ProgramStep(
                tool="run_sql", args={"query": "SELECT v FROM $docs ORDER BY id"}, bind="final"
            ),
        ]
    )
    result = await run_program(program, ctx, registry)
    assert result.ok
    assert result.steps[0].handle_table == "program_docs"
    assert result.steps[0].rows == 2
    # The temp table is real and queryable outside the program.
    df = conn.execute("SELECT COUNT(*) AS n FROM program_docs")
    assert df["n"].to_list() == [2]
    # Step 2's SQL read the substituted temp table and itself materialized.
    assert result.steps[1].ok
    assert result.steps[1].rows == 2
    assert result.final_bind == "final"
    assert result.final_table == "program_final"
    df2 = conn.execute("SELECT v FROM program_final ORDER BY v")
    assert df2["v"].to_list() == ["sentinel_cell_a", "sentinel_cell_b"]
    conn.disconnect()


async def test_program_result_is_bounded_no_cell_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_sql_mod, "_history_log", QueryHistoryLog(history_dir=tmp_path))
    conn = _make_duckdb(tmp_path)
    registry = ToolRegistry()
    registry.register(_TableTool())
    ctx = ToolContext(connection=conn, catalog=None)
    program = Program(steps=[ProgramStep(tool="make_table", args={}, bind="docs")])
    result = await run_program(program, ctx, registry)
    assert result.ok
    dumped = result.model_dump_json()
    assert "sentinel_cell_a" not in dumped  # NEVER embeds row data
    assert "sentinel_cell_b" not in dumped
    conn.disconnect()


async def test_table_step_on_non_duckdb_primary_is_structured_failure() -> None:
    registry = ToolRegistry()
    registry.register(_TableTool())
    ctx = ToolContext(connection=object(), catalog=None)
    program = Program(
        steps=[
            ProgramStep(tool="make_table", args={}, bind="docs"),
            ProgramStep(tool="make_table", args={}, bind="never"),
        ]
    )
    result = await run_program(program, ctx, registry)
    assert not result.ok
    assert len(result.steps) == 1  # stop-on-error
    assert result.steps[0].ok is False
    assert result.steps[0].error is not None
    assert "DuckDB" in result.steps[0].error
