"""Program interpreter: sequential registry dispatch with handle binding.

Safe by construction — the ONLY thing the interpreter does with a step is
``registry.dispatch(step.tool, resolved_args, ctx)``, so every existing gate
(read-only ``is_mutating``, per-tool caps, input validation) applies per step.
Only the bounded :class:`ProgramResult` summary is ever returned; intermediate
payloads never round-trip through model context.
"""

from __future__ import annotations

from typing import Any

import polars as pl
from pydantic import BaseModel

from labrat.agent.program.dsl import Program, ProgramError, ResolvedHandle, resolve_refs
from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.agent.tools.serialization import LedgerPayloadProvider
from labrat.db.duckdb_engine import DuckDBConnection

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
    """Run a program's steps sequentially, binding each result to its handle.

    Stop-on-error: the first failing step (dispatch failure, structured
    ``ok=False`` tool output, a bad $ref, or a table result the primary
    connection cannot materialize) is recorded and later steps never dispatch.
    A step whose output declares a ``("table", df)`` ledger payload is
    materialized as the DuckDB TEMP table ``program_<bind>``.
    """
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
    final_table: str | None = None

    for index, step in enumerate(program.steps):
        try:
            resolved_args = resolve_refs(step.args, handles)
        except ProgramError as exc:
            summaries.append(
                StepSummary(index=index, tool=step.tool, ok=False, bind=step.bind, error=str(exc))
            )
            return ProgramResult(
                ok=False,
                steps=summaries,
                final_bind=final_bind,
                final_table=final_table,
                error=f"step {index} ({step.tool}): {exc}",
            )

        dispatch = await registry.dispatch(step.tool, resolved_args, ctx)

        step_ok = dispatch.ok
        error = dispatch.error
        dump: dict[str, Any] = {}
        if dispatch.ok and isinstance(dispatch.value, BaseModel):
            dump = dispatch.value.model_dump()
            if dump.get("ok") is False:
                # Tools like run_sql/llm_extract report failure as a structured
                # ok=False output without raising — still a failed step.
                step_ok = False
                err_val = dump.get("error")
                error = err_val if isinstance(err_val, str) else "tool reported ok=False"

        handle_table: str | None = None
        if step_ok and isinstance(dispatch.value, LedgerPayloadProvider):
            payload = dispatch.value.ledger_payload()
            if payload is not None:
                kind, obj = payload
                if kind == "table" and isinstance(obj, pl.DataFrame):
                    conn = ctx.connections.get(ctx.primary)
                    if isinstance(conn, DuckDBConnection):
                        handle_table = f"program_{step.bind}"
                        try:
                            conn.materialize_table(handle_table, obj.to_arrow())  # type: ignore[arg-type]
                        except Exception as exc:
                            step_ok = False
                            error = f"failed to materialize handle ${step.bind}: {exc}"
                            handle_table = None
                    else:
                        step_ok = False
                        error = (
                            f"step produced a table but the primary connection is "
                            f"{type(conn).__name__}, not DuckDB — cannot materialize ${step.bind}"
                        )

        summaries.append(
            StepSummary(
                index=index,
                tool=step.tool,
                ok=step_ok,
                bind=step.bind,
                handle_table=handle_table,
                rows=_rows_of(dump),
                rows_failed=_int_or_none(dump.get("rows_failed")),
                error=error,
            )
        )

        if not step_ok:
            return ProgramResult(
                ok=False,
                steps=summaries,
                final_bind=final_bind,
                final_table=final_table,
                error=f"step {index} ({step.tool}) failed: {error}",
            )

        handles[step.bind] = ResolvedHandle(table=handle_table, output=dump)
        final_bind = step.bind
        final_table = handle_table

    return ProgramResult(ok=True, steps=summaries, final_bind=final_bind, final_table=final_table)
