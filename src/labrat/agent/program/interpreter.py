"""Program interpreter: sequential registry dispatch with handle binding.

Safe by construction — the ONLY thing the interpreter does with a step is
``registry.dispatch(step.tool, resolved_args, ctx)``, so every existing gate
(read-only ``is_mutating``, per-tool caps, input validation) applies per step.
Only the bounded :class:`ProgramResult` summary is ever returned; intermediate
payloads never round-trip through model context.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from labrat.agent.program.dsl import Program, ResolvedHandle, resolve_refs
from labrat.agent.tools.base import ToolContext, ToolRegistry

DEFAULT_MAX_STEPS = 20


class StepSummary(BaseModel):
    """Bounded per-step record — never carries rows or payloads."""

    index: int
    tool: str
    ok: bool
    bind: str
    handle_table: str | None = None
    rows: int | None = None
    rows_failed: int | None = None
    error: str | None = None


class ProgramResult(BaseModel):
    """Bounded program outcome — the only thing that returns to model context."""

    ok: bool
    steps: list[StepSummary] = []
    final_bind: str | None = None
    final_table: str | None = None
    error: str | None = None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _rows_of(dump: dict[str, Any]) -> int | None:
    """Row count from a step output's dump: run_sql row_count / llm_* rows_processed."""
    for key in ("row_count", "rows_processed"):
        v = _int_or_none(dump.get(key))
        if v is not None:
            return v
    return None


async def run_program(
    program: Program,
    ctx: ToolContext,
    registry: ToolRegistry,
    *,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> ProgramResult:
    """Run a program's steps sequentially, binding each result to its handle."""
    if len(program.steps) > max_steps:
        return ProgramResult(
            ok=False,
            error=(
                f"program has {len(program.steps)} steps, exceeding max_steps={max_steps}; "
                "nothing was run"
            ),
        )

    handles: dict[str, ResolvedHandle] = {}
    summaries: list[StepSummary] = []
    final_bind: str | None = None

    for index, step in enumerate(program.steps):
        resolved_args = resolve_refs(step.args, handles)
        dispatch = await registry.dispatch(step.tool, resolved_args, ctx)

        dump: dict[str, Any] = {}
        if dispatch.ok and isinstance(dispatch.value, BaseModel):
            dump = dispatch.value.model_dump()

        summaries.append(
            StepSummary(
                index=index,
                tool=step.tool,
                ok=dispatch.ok,
                bind=step.bind,
                rows=_rows_of(dump),
                rows_failed=_int_or_none(dump.get("rows_failed")),
                error=dispatch.error,
            )
        )
        handles[step.bind] = ResolvedHandle(table=None, output=dump)
        final_bind = step.bind

    return ProgramResult(ok=True, steps=summaries, final_bind=final_bind, final_table=None)
