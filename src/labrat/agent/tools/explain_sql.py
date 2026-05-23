"""explain_sql tool: return the query plan for a SQL statement."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel

from labrat.agent.tools.base import Tool, ToolContext
from labrat.db.base import Connection


class _Input(BaseModel):
    query: str


class _Output(BaseModel):
    query: str
    plan: str


class ExplainSqlTool(Tool[_Input]):
    """Return the query execution plan without running the query."""

    @property
    def name(self) -> str:
        return "explain_sql"

    @property
    def description(self) -> str:
        return (
            "Return the query execution plan for a SQL statement. "
            "Use this to check if a query is valid and understand its cost before running it."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        conn = cast(Connection, ctx.connection)
        plan = conn.explain(args.query)
        return _Output(query=args.query, plan=plan)
