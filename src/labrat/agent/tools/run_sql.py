"""run_sql tool: execute SQL with mutation refusal, auto-limit, and history logging."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

import polars as pl
import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp
from sqlglot.errors import ParseError

from labrat.agent.tools.base import Tool, ToolContext
from labrat.db.base import Connection
from labrat.history.events import QueryEvent
from labrat.history.log import QueryHistoryLog

_SAFE_STATEMENT_TYPES = (exp.Select, exp.Union, exp.Intersect, exp.Except)
_history_log = QueryHistoryLog()


def _unwrap_with(node: object) -> object:
    """Peel off WITH wrappers to reach the inner statement."""
    while isinstance(node, exp.With):
        node = node.this
    return node


def _statement_count(sql: str) -> int:
    """Count top-level SQL statements.

    ``_is_mutation`` / ``_has_limit`` use ``parse_one``, which only sees the FIRST
    statement — so ``SELECT 1; DROP TABLE t`` would slip past the mutation check.
    This counts every statement so the caller can refuse statement-stacking. Returns
    1 on parse error (fail-open: let the DB reject genuinely malformed SQL).
    """
    try:
        parsed = sqlglot.parse(sql.strip())
    except ParseError:
        return 1
    return sum(1 for stmt in parsed if stmt is not None)


def _is_mutation(sql: str) -> bool:
    """Return True if *sql* is a data-mutating or DDL statement."""
    try:
        root = _unwrap_with(sqlglot.parse_one(sql.strip()))
        return not isinstance(root, _SAFE_STATEMENT_TYPES)
    except ParseError:
        return False  # parse error → let the DB handle it; don't block valid queries


def _has_limit(sql: str) -> bool:
    """Return True if the statement already contains a LIMIT clause."""
    try:
        root = _unwrap_with(sqlglot.parse_one(sql.strip()))
        if not isinstance(root, exp.Select):
            return False
        return root.find(exp.Limit) is not None
    except ParseError:
        return False


def _apply_limit(sql: str, limit: int) -> str:
    stripped = sql.rstrip().rstrip(";").rstrip()
    return f"{stripped}\nLIMIT {limit}"


def _classify_sql_error(message: str) -> tuple[str, str]:
    """Classify a DB exception message into (category, remediation hint).

    Deterministic substring matching — dialect-agnostic enough for the DuckDB primary;
    the categories generalize. Order matters: the column check (which requires "column")
    runs before the table check so "column ... does not exist" is not mis-tagged.
    """
    m = message.lower()
    if "column" in m and (
        "not found" in m or "does not have a column" in m or "does not exist" in m
    ):
        return (
            "missing_column",
            "Column not found — call describe_table / search_columns to confirm the column name.",
        )
    if ("table" in m or "catalog" in m) and ("does not exist" in m or "not found" in m):
        return (
            "unknown_table",
            "Table not found — call list_tables / profile_dataset to confirm the table name.",
        )
    if "parser error" in m or "syntax error" in m:
        return ("syntax", "Syntax error — re-check the SQL against the active dialect.")
    if "conversion" in m or "cast" in m or "type mismatch" in m or "no function matches" in m:
        return (
            "type_mismatch",
            "Type mismatch — check column types with describe_table and cast explicitly.",
        )
    return (
        "other",
        "Inspect the error; verify table/column names and types before retrying.",
    )


def _log(
    profile: str,
    thread_id: str,
    version_id: str,
    sql: str,
    executed: bool,
    success: bool | None = None,
    execution_time_ms: float | None = None,
    row_count: int | None = None,
    error_message: str | None = None,
) -> None:
    event = QueryEvent(
        timestamp=datetime.now(tz=UTC),
        profile=profile,
        thread_id=thread_id,
        version_id=version_id,
        sql_final=sql,
        executed=executed,
        success=success,
        execution_time_ms=int(execution_time_ms) if execution_time_ms is not None else None,
        row_count=row_count,
        error_message=error_message,
    )
    _history_log.append(event)


class _Input(BaseModel):
    query: str
    auto_limit: int = 1000
    force: bool = False
    database: str | None = Field(
        default=None,
        description="Connection name when multiple databases are available; defaults to primary.",
    )


class _Output(BaseModel):
    ok: bool
    query: str
    columns: list[str] | None = None
    rows: list[list[str]] | None = None
    row_count: int | None = None
    refused: bool = False
    needs_confirmation: bool = False
    error: str | None = None
    error_category: str | None = None
    executed_sql: str | None = None
    hint: str | None = None
    warnings: list[str] = []


class RunSqlTool(Tool[_Input]):
    """Execute a SQL query.

    Safety layers applied in order:
    1. Single-statement guard — statement-stacking (e.g. "SELECT 1; DROP TABLE t")
       is refused outright (not force-bypassable); closes the parse_one blind spot.
    2. Mutation refusal — DDL/DML statements return ok=False unless force=True.
    3. Auto-limit — LIMIT is appended when the query has none (default 1000 rows).
    4. Every execution (success or failure) is written to the query history log.
    """

    def __init__(
        self,
        on_result: Callable[[pl.DataFrame, float], None] | None = None,
        on_draft: Callable[[str], None] | None = None,
    ) -> None:
        self._on_result = on_result
        self._on_draft = on_draft

    @property
    def name(self) -> str:
        return "run_sql"

    @property
    def description(self) -> str:
        return (
            "Execute a SELECT query and return the results. "
            "Submit ONE statement per call (statement-stacking is refused). "
            "DDL and DML statements (INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE) "
            "are refused unless force=True. A LIMIT is automatically applied when missing. "
            "A `normalize_text(col)` SQL function is available on the DuckDB session for "
            "case/diacritic/whitespace-insensitive matching (e.g. "
            "`WHERE normalize_text(name) = normalize_text('...')`)."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        thread_id = getattr(ctx, "thread_id", "unknown")
        version_id = getattr(ctx, "version_id", "unknown")

        # Structural guard: refuse statement-stacking (e.g. "SELECT 1; DROP TABLE t").
        # This is not force-bypassable — it's a single-statement contract, distinct
        # from the mutation force-override, and closes the parse_one blind spot.
        if _statement_count(args.query) > 1:
            _log(
                profile=ctx.profile_name,
                thread_id=thread_id,
                version_id=version_id,
                sql=args.query,
                executed=False,
                success=False,
                error_message="multiple statements refused",
            )
            return _Output(
                ok=False,
                query=args.query,
                refused=True,
                error=(
                    "Multiple SQL statements are not allowed; submit a single statement per call."
                ),
            )

        if _is_mutation(args.query) and not args.force:
            _log(
                profile=ctx.profile_name,
                thread_id=thread_id,
                version_id=version_id,
                sql=args.query,
                executed=False,
                success=False,
                error_message="mutation refused",
            )
            return _Output(
                ok=False,
                query=args.query,
                refused=True,
                error=(
                    "Mutation statements (INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/TRUNCATE) "
                    "are not allowed. Set force=True to override on a writable connection."
                ),
            )

        sql = args.query if _has_limit(args.query) else _apply_limit(args.query, args.auto_limit)
        if self._on_draft is not None:
            self._on_draft(sql)

        conn = cast(Connection, ctx.connections[args.database or ctx.primary])
        t0 = time.monotonic()
        try:
            df = conn.execute(sql)
            elapsed_ms = (time.monotonic() - t0) * 1000
        except Exception as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            category, hint = _classify_sql_error(str(exc))
            _log(
                profile=ctx.profile_name,
                thread_id=thread_id,
                version_id=version_id,
                sql=sql,
                executed=True,
                success=False,
                execution_time_ms=elapsed_ms,
                error_message=str(exc),
            )
            return _Output(
                ok=False,
                query=args.query,
                error=str(exc),
                error_category=category,
                executed_sql=sql,
                hint=hint,
            )

        rows = [[str(v) if v is not None else "" for v in row] for row in df.iter_rows()]
        _log(
            profile=ctx.profile_name,
            thread_id=thread_id,
            version_id=version_id,
            sql=sql,
            executed=True,
            success=True,
            execution_time_ms=elapsed_ms,
            row_count=len(df),
        )
        if self._on_result is not None:
            self._on_result(df, elapsed_ms)

        warnings: list[str] = []
        lowered = sql.lower()
        if len(df) == 0 and re.search(r"\b(where|join)\b", lowered):
            warnings.append(
                "Query returned 0 rows despite a WHERE/JOIN — check the predicate and join keys."
            )
        elif len(df) > 0:
            for col in df.columns:
                if df[col].null_count() == len(df):
                    warnings.append(
                        f"Column `{col}` is entirely NULL in the result — "
                        "likely a bad join or wrong column."
                    )

        return _Output(
            ok=True,
            query=args.query,
            columns=df.columns,
            rows=rows,
            row_count=len(df),
            warnings=warnings,
        )
