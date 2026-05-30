"""CreateChartTool: run a query and render as a unicode chart (M21)."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import polars as pl
from pydantic import BaseModel, Field

from labrat.agent.tools.base import Tool, ToolContext
from labrat.chart.render_unicode import render_unicode
from labrat.chart.spec import ChartSpec, ChartType
from labrat.db.base import Connection


class _Input(BaseModel):
    query: str = Field(description="SQL SELECT query whose results will be charted.")
    chart_type: str = Field(description="Chart type: bar, line, scatter, area, or histogram.")
    x: str = Field(description="Column name for the X axis.")
    y: str = Field(description="Column name for the Y axis (must be numeric).")
    title: str | None = Field(default=None, description="Optional chart title.")
    database: str | None = Field(
        default=None,
        description="Connection name when multiple databases are available; defaults to primary.",
    )


class CreateChartTool(Tool[_Input]):
    """Execute a SQL query and render the results as a unicode chart.

    The chart appears in the results pane. Use to visualise aggregated data.
    """

    def __init__(self, on_chart: Callable[[str], None] | None = None) -> None:
        self._on_chart = on_chart

    @property
    def name(self) -> str:
        return "create_chart"

    @property
    def description(self) -> str:
        return (
            "Execute a SQL query and render the results as a chart in the results pane. "
            "chart_type must be one of: bar, line, scatter, area, histogram."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> object:
        try:
            chart_type = ChartType(args.chart_type)
        except ValueError:
            return {
                "ok": False,
                "error": (
                    f"Unknown chart_type {args.chart_type!r}. "
                    "Use: bar, line, scatter, area, histogram."
                ),
            }

        conn = cast(Connection, ctx.connections[args.database or ctx.primary])
        try:
            df: pl.DataFrame = conn.execute(args.query)
        except Exception as exc:
            return {"ok": False, "error": f"Query failed: {exc}"}

        spec = ChartSpec(chart_type=chart_type, x=args.x, y=args.y, title=args.title)
        try:
            chart_str = render_unicode(spec, df)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        if self._on_chart is not None:
            self._on_chart(chart_str)

        return {"status": "rendered", "chart_type": args.chart_type, "rows": len(df)}
