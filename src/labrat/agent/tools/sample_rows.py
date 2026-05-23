"""sample_rows tool: return a small sample of rows from a table."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel

from labrat.agent.tools.base import Tool, ToolContext
from labrat.db.base import Connection


class _Input(BaseModel):
    table: str
    n: int = 10


class _Output(BaseModel):
    table_name: str
    row_count: int
    columns: list[str]
    rows: list[list[str]]


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
        conn = cast(Connection, ctx.connection)
        df = conn.sample_table(args.table, n=args.n)
        rows = [[str(v) if v is not None else "" for v in row] for row in df.iter_rows()]
        return _Output(
            table_name=args.table,
            row_count=len(df),
            columns=df.columns,
            rows=rows,
        )
