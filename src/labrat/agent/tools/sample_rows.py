"""sample_rows tool: return a small sample of rows from a table."""

from __future__ import annotations

from typing import cast

import polars as pl
from pydantic import BaseModel, Field, PrivateAttr

from labrat.agent.tools.base import Tool, ToolContext, stringify_rows
from labrat.agent.tools.serialization import LedgerPayloadKind
from labrat.db.base import Connection


class _Input(BaseModel):
    table: str
    n: int = 10
    database: str | None = Field(
        default=None,
        description="Connection name when multiple databases are available; defaults to primary.",
    )


class _Output(BaseModel):
    table_name: str
    row_count: int
    columns: list[str]
    rows: list[list[str]]

    # Sampled frame carried outside the serialised surface for the ContextLedger.
    # PrivateAttr → excluded from model_dump/JSON and from str(); off-ledger
    # behavior is unchanged.
    _result_df: pl.DataFrame | None = PrivateAttr(default=None)

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
        if self._result_df is not None:
            return ("table", self._result_df)
        return None

    def attach_result_df(self, df: pl.DataFrame) -> None:
        """Set the private result frame from outside the class (pyright-clean)."""
        self._result_df = df


class SampleRowsTool(Tool[_Input]):
    """Sample a small number of rows from a table to understand its contents."""

    @property
    def name(self) -> str:
        return "sample_rows"

    @property
    def description(self) -> str:
        return (
            "Return a sample of rows from a table. "
            "Use this to inspect actual data values, understand column formats, "
            "and discover common values before writing a query."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        conn = cast(Connection, ctx.connections[args.database or ctx.primary])
        df = conn.sample_table(args.table, n=args.n)
        rows = stringify_rows(df)
        out = _Output(
            table_name=args.table,
            row_count=len(df),
            columns=df.columns,
            rows=rows,
        )
        out.attach_result_df(df)
        return out
