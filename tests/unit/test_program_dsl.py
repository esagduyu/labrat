"""Program DSL models + handle resolution: pure, no DB, no LLM."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from labrat.agent.program.dsl import Program, ProgramStep


def test_program_step_holds_tool_args_bind() -> None:
    step = ProgramStep(tool="run_sql", args={"query": "SELECT 1"}, bind="docs")
    assert step.tool == "run_sql"
    assert step.args == {"query": "SELECT 1"}
    assert step.bind == "docs"


def test_program_step_args_default_empty() -> None:
    step = ProgramStep(tool="list_tables", bind="t")
    assert step.args == {}


def test_bind_rejects_unsafe_ident() -> None:
    with pytest.raises(ValidationError, match="alphanumeric"):
        ProgramStep(tool="run_sql", args={}, bind="bad name; drop")


def test_bind_rejects_empty() -> None:
    with pytest.raises(ValidationError):
        ProgramStep(tool="run_sql", args={}, bind="")


def test_duplicate_bind_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate bind"):
        Program(
            steps=[
                ProgramStep(tool="run_sql", args={"query": "SELECT 1"}, bind="x"),
                ProgramStep(tool="run_sql", args={"query": "SELECT 2"}, bind="x"),
            ]
        )


def test_empty_program_rejected() -> None:
    with pytest.raises(ValidationError):
        Program(steps=[])


def test_valid_program_accepted() -> None:
    program = Program(
        steps=[
            ProgramStep(tool="run_sql", args={"query": "SELECT 1"}, bind="a"),
            ProgramStep(tool="run_sql", args={"query": "SELECT 2"}, bind="b"),
        ]
    )
    assert [s.bind for s in program.steps] == ["a", "b"]
