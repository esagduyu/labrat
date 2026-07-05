"""llm_extract tool: per-row LLM field extraction over a text column.

One of the codebase's first LLM-calling tools (with llm_classify) — an
intentional, bounded departure; every other tool is deterministic. Functional
ONLY where ``ctx.llm_fn`` is injected (the labrat-agent / AgentLoop path via
``run_agent_task``); everywhere else (claude-mcp, MCP server, TUI) it
self-errors with a structured ``ok=False`` result. Results are materialized as
a DuckDB TEMP table (joinable via run_sql) and declared to the Context Ledger
via ``ledger_payload()``.
"""

from __future__ import annotations

from typing import Any

import polars as pl
from pydantic import BaseModel, Field, PrivateAttr

from labrat.agent.tools.base import Tool, ToolContext
from labrat.agent.tools.llm_primitives import extract_rows
from labrat.agent.tools.serialization import LedgerPayloadKind
from labrat.db.duckdb_engine import DuckDBConnection

DEFAULT_EXTRACT_RESULT_TABLE = "llm_extract_result"

_NO_LLM_ERROR = (
    "llm_extract requires an LLM-enabled context (no llm_fn is injected on this path). "
    "Use run_sql string functions (regexp_extract, string_split, ...) instead."
)


class _Input(BaseModel):
    table: str = Field(description="Source table (or temp table) holding the text column.")
    text_column: str = Field(description="Column of unstructured text to extract from.")
    json_schema: dict[str, Any] = Field(
        description=(
            "JSON schema of the fields to extract, e.g. "
            '{"properties": {"inventor": {"type": "string"}}}. '
            "Extracted columns are stored as VARCHAR."
        )
    )
    key_columns: list[str] = Field(
        default_factory=list,
        description="Key columns carried into the result table so it can be joined back.",
    )
    where: str | None = Field(default=None, description="Optional SQL WHERE fragment.")
    limit: int | None = Field(
        default=None,
        description="Optional row cap; always clamped to the hard max of 200 rows.",
    )
    result_table: str | None = Field(
        default=None,
        description="Result temp-table name (default 'llm_extract_result').",
    )


class _Output(BaseModel):
    ok: bool
    result_table: str | None = None
    rows_processed: int = 0
    rows_failed: int = 0
    columns: list[str] = []
    error: str | None = None

    # The extracted result frame, carried outside the serialised surface so the
    # ContextLedger can store it as a Parquet artifact. PrivateAttr → excluded
    # from model_dump/JSON and from str(); off-ledger behavior is unchanged.
    _result_df: pl.DataFrame | None = PrivateAttr(default=None)

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
        if self.ok and self._result_df is not None:
            return ("table", self._result_df)
        return None

    def attach_result_df(self, df: pl.DataFrame) -> None:
        """Set the private result frame from outside the class (pyright-clean)."""
        self._result_df = df


class LlmExtractTool(Tool[_Input]):
    """Fan out one LLM call per row to extract structured fields from text."""

    mutating = True  # materializes a (temp) result table

    @property
    def name(self) -> str:
        return "llm_extract"

    @property
    def description(self) -> str:
        return (
            "Extract structured fields from an unstructured text column, one LLM call "
            "per row (hard cap 200 rows). Provide a JSON schema of the fields; the "
            "result is materialized as a DuckDB temp table (default "
            "'llm_extract_result') with your key_columns plus one VARCHAR column per "
            "field, joinable with run_sql. Rows whose extraction fails are kept with "
            "NULL fields and counted in rows_failed. Only available when the agent "
            "runtime injects an LLM; otherwise returns a structured error."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        if ctx.llm_fn is None:
            return _Output(ok=False, error=_NO_LLM_ERROR)
        conn = ctx.connection
        if not isinstance(conn, DuckDBConnection):
            return _Output(
                ok=False,
                error=(
                    f"llm_extract requires a DuckDB primary connection; got {type(conn).__name__}."
                ),
            )
        result_table = args.result_table or DEFAULT_EXTRACT_RESULT_TABLE
        # Same guard as materialize_table, applied up-front so a bad name fails
        # BEFORE any per-row LLM calls are spent.
        if not result_table.replace("_", "").isalnum():
            return _Output(
                ok=False,
                error=f"result_table must be alphanumeric/underscore: {result_table!r}",
            )
        try:
            result = await extract_rows(
                ctx,
                table=args.table,
                text_column=args.text_column,
                key_columns=args.key_columns,
                spec=args.json_schema,
                where=args.where,
                limit=args.limit,
            )
            conn.materialize_table(result_table, result.df.to_arrow())  # type: ignore[arg-type]
        except Exception as exc:
            return _Output(ok=False, error=str(exc))
        out = _Output(
            ok=True,
            result_table=result_table,
            rows_processed=result.rows_processed,
            rows_failed=result.rows_failed,
            columns=result.df.columns,
        )
        out.attach_result_df(result.df)
        return out
