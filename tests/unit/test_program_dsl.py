"""Program DSL models + handle resolution: pure, no DB, no LLM."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from labrat.agent.program.dsl import (
    Program,
    ProgramError,
    ProgramStep,
    ResolvedHandle,
    resolve_refs,
)


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


# ── resolve_refs ────────────────────────────────────────────────────────────


def _handles() -> dict[str, ResolvedHandle]:
    return {
        "docs": ResolvedHandle(table="program_docs", output={"ok": True, "row_count": 3}),
        "facts": ResolvedHandle(
            table="program_facts",
            output={"ok": True, "rows_processed": 3, "rows_failed": 1},
        ),
        "note": ResolvedHandle(table=None, output={"ok": True, "row_count": 0}),
    }


def test_whole_string_bare_handle_resolves_to_table_name() -> None:
    out = resolve_refs({"table": "$docs"}, _handles())
    assert out == {"table": "program_docs"}


def test_whole_string_field_ref_resolves_to_native_value() -> None:
    out = resolve_refs({"n": "$facts.rows_failed"}, _handles())
    assert out == {"n": 1}  # native int, not "1"


def test_embedded_refs_substituted_inline() -> None:
    out = resolve_refs(
        {"query": "SELECT d.id, f.drug FROM $facts f JOIN $docs d USING (id)"},
        _handles(),
    )
    assert out == {
        "query": "SELECT d.id, f.drug FROM program_facts f JOIN program_docs d USING (id)"
    }


def test_embedded_field_ref_substituted_as_str() -> None:
    out = resolve_refs({"query": "SELECT * FROM t LIMIT $facts.rows_failed"}, _handles())
    assert out == {"query": "SELECT * FROM t LIMIT 1"}


def test_dollar_digit_literal_untouched() -> None:
    out = resolve_refs({"query": "SELECT * FROM t WHERE price > $100"}, _handles())
    assert out == {"query": "SELECT * FROM t WHERE price > $100"}


def test_recurses_through_nested_dicts_and_lists() -> None:
    out = resolve_refs(
        {"outer": {"tables": ["$docs", "$facts"], "keep": 7}, "flag": True},
        _handles(),
    )
    assert out == {"outer": {"tables": ["program_docs", "program_facts"], "keep": 7}, "flag": True}


def test_non_string_scalars_pass_through() -> None:
    out = resolve_refs({"limit": 5, "force": False, "opt": None}, _handles())
    assert out == {"limit": 5, "force": False, "opt": None}


def test_unknown_handle_raises_program_error() -> None:
    with pytest.raises(ProgramError, match="unknown handle"):
        resolve_refs({"table": "$nope"}, _handles())


def test_embedded_unknown_handle_raises_program_error() -> None:
    with pytest.raises(ProgramError, match="unknown handle"):
        resolve_refs({"query": "SELECT * FROM $nope x"}, _handles())


def test_missing_field_raises_program_error() -> None:
    with pytest.raises(ProgramError, match="no field"):
        resolve_refs({"n": "$docs.nonexistent"}, _handles())


def test_bare_ref_to_tableless_handle_raises_program_error() -> None:
    with pytest.raises(ProgramError, match="produced no table"):
        resolve_refs({"table": "$note"}, _handles())
