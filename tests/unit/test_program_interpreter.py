"""Program interpreter: sequential dispatch, handles, stop-on-error, max-steps."""

from __future__ import annotations

from pydantic import BaseModel

from labrat.agent.program.dsl import Program, ProgramStep
from labrat.agent.program.interpreter import (
    DEFAULT_MAX_STEPS,
    ProgramResult,
    StepSummary,
    run_program,
)
from labrat.agent.tools.base import Tool, ToolContext, ToolRegistry

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
