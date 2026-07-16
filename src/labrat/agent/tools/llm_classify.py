"""llm_classify tool: per-row LLM classification of a text column into fixed labels.

One of the codebase's first LLM-calling tools (with llm_extract) — an
intentional, bounded departure; every other tool is deterministic. Functional
ONLY where ``ctx.llm_fn`` is injected (the labrat-agent / AgentLoop path via
``run_agent_task``); everywhere else (claude-mcp, MCP server, TUI) it
self-errors with a structured ``ok=False`` result. Results are materialized as
a DuckDB TEMP table (joinable via run_sql) and declared to the Context Ledger
via ``ledger_payload()``.
"""

from __future__ import annotations

import polars as pl
from pydantic import BaseModel, Field, PrivateAttr

from labrat.agent.tools.base import Tool, ToolContext
from labrat.agent.tools.llm_primitives import (
    MAX_BATCHED_CLASSIFY_ROWS,
    MAX_CLASSIFY_BATCH_SIZE,
    MAX_NESTED_REQUESTS,
    extract_rows,
)
from labrat.agent.tools.serialization import LedgerPayloadKind
from labrat.db.duckdb_engine import DuckDBConnection

DEFAULT_CLASSIFY_RESULT_TABLE = "llm_classify_result"

_NO_LLM_ERROR = (
    "llm_classify requires an LLM-enabled context (no llm_fn is injected on this path). "
    "Use run_sql CASE/string expressions instead."
)


class _Input(BaseModel):
    table: str = Field(description="Source table (or temp table) holding the text column.")
    text_column: str = Field(description="Column of unstructured text to classify.")
    labels: list[str] = Field(
        description=(
            "Allowed categories. Each row's category is constrained to this list; "
            "an out-of-list reply becomes a NULL row counted in rows_failed."
        )
    )
    key_columns: list[str] = Field(
        default_factory=list,
        description="Key columns carried into the result table so it can be joined back.",
    )
    where: str | None = Field(default=None, description="Optional SQL WHERE fragment.")
    limit: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Optional row cap. Non-batched calls are clamped to 200 rows; batched "
            "calls are clamped by batch_size * max_requests and the 20,000-row hard max."
        ),
    )
    batch_size: int = Field(
        default=1,
        ge=1,
        le=MAX_CLASSIFY_BATCH_SIZE,
        description=(
            "Texts classified per nested LLM request. Default 1 preserves per-row "
            "behavior; use up to 50 for large datasets."
        ),
    )
    max_requests: int = Field(
        default=MAX_NESTED_REQUESTS,
        ge=1,
        le=MAX_NESTED_REQUESTS,
        description=(
            "Hard budget for nested LLM requests in this tool call (maximum 400). "
            "The selected row count is clamped before execution so this cannot be exceeded."
        ),
    )
    result_table: str | None = Field(
        default=None,
        description="Result temp-table name (default 'llm_classify_result').",
    )


class _Output(BaseModel):
    ok: bool
    result_table: str | None = None
    rows_processed: int = 0
    rows_failed: int = 0
    requests_made: int = 0
    columns: list[str] = []
    error: str | None = None

    # The classified result frame, carried outside the serialised surface so the
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


class LlmClassifyTool(Tool[_Input]):
    """Classify rows individually or in strict, request-budgeted batches."""

    mutating = True  # materializes a (temp) result table

    @property
    def name(self) -> str:
        return "llm_classify"

    @property
    def description(self) -> str:
        return (
            "Classify an unstructured text column into a fixed set of labels. The "
            "default is one LLM call per row with a 200-row cap. For large tables, set "
            "batch_size up to 50; batched mode can cover up to 20,000 rows while "
            "max_requests enforces at most 400 nested calls. The result is materialized "
            "as a DuckDB "
            "temp table (default 'llm_classify_result') with your key_columns plus a "
            "VARCHAR 'category' column constrained to the labels, joinable with "
            "run_sql. Rows whose classification fails (including out-of-label replies) "
            "are kept with a NULL category and counted in rows_failed. Only available "
            "when the agent runtime injects an LLM; otherwise returns a structured "
            "error."
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
                    f"llm_classify requires a DuckDB primary connection; got {type(conn).__name__}."
                ),
            )
        result_table = args.result_table or DEFAULT_CLASSIFY_RESULT_TABLE
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
                spec=args.labels,
                where=args.where,
                limit=args.limit,
                max_rows=(
                    MAX_BATCHED_CLASSIFY_ROWS if args.batch_size > 1 else 200
                ),
                classify_batch_size=args.batch_size,
                max_requests=args.max_requests,
            )
            conn.materialize_table(result_table, result.df.to_arrow())  # type: ignore[arg-type]
        except Exception as exc:
            return _Output(ok=False, error=str(exc))
        out = _Output(
            ok=True,
            result_table=result_table,
            rows_processed=result.rows_processed,
            rows_failed=result.rows_failed,
            requests_made=result.requests_made,
            columns=result.df.columns,
        )
        out.attach_result_df(result.df)
        return out
