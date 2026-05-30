"""column_stats tool: return basic statistics for a single column."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, Field

from labrat.agent.tools.base import Tool, ToolContext
from labrat.db.base import Connection


class _Input(BaseModel):
    table: str
    column: str
    database: str | None = Field(
        default=None,
        description="Connection name when multiple databases are available; defaults to primary.",
    )


class _Output(BaseModel):
    column_name: str
    table_name: str
    data_type: str
    null_count: int
    distinct_count: int
    min_value: str | None = None
    max_value: str | None = None


class ColumnStatsTool(Tool[_Input]):
    """Return min, max, null count, and distinct count for a specific column."""

    @property
    def name(self) -> str:
        return "column_stats"

    @property
    def description(self) -> str:
        return (
            "Return basic statistics for a column: null count, distinct count, "
            "and min/max values. "
            "Use this to understand value ranges and data quality before filtering or aggregating."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        conn = cast(Connection, ctx.connections[args.database or ctx.primary])
        stats = conn.column_stats(args.table, args.column)
        return _Output(
            column_name=stats.column_name,
            table_name=stats.table_name,
            data_type=stats.data_type,
            null_count=stats.null_count,
            distinct_count=stats.distinct_count,
            min_value=stats.min_value,
            max_value=stats.max_value,
        )
