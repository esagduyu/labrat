"""run_sql tool: execute SQL with mutation refusal, auto-limit, and history logging."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import cast

import sqlglot
from pydantic import BaseModel
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


class _Output(BaseModel):
    ok: bool
    query: str
    columns: list[str] | None = None
    rows: list[list[str]] | None = None
    row_count: int | None = None
    refused: bool = False
    needs_confirmation: bool = False
    error: str | None = None


class RunSqlTool(Tool[_Input]):
    """Execute a SQL query.

    Safety layers applied in order:
    1. Mutation refusal — DDL/DML statements return ok=False unless force=True.
    2. Auto-limit — LIMIT is appended when the query has none (default 1000 rows).
    3. Every execution (success or failure) is written to the query history log.
    """

    @property
    def name(self) -> str:
        return "run_sql"

    @property
    def description(self) -> str:
        return (
            "Execute a SELECT query and return the results. "
            "DDL and DML statements (INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE) "
            "are refused unless force=True. A LIMIT is automatically applied when missing."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        thread_id = getattr(ctx, "thread_id", "unknown")
        version_id = getattr(ctx, "version_id", "unknown")

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

        conn = cast(Connection, ctx.connection)
        t0 = time.monotonic()
        try:
            df = conn.execute(sql)
            elapsed_ms = (time.monotonic() - t0) * 1000
        except Exception as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
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
            return _Output(ok=False, query=args.query, error=str(exc))

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
        return _Output(
            ok=True,
            query=args.query,
            columns=df.columns,
            rows=rows,
            row_count=len(df),
        )
