"""SearchQueryHistoryTool: surface past queries as agent context (M28)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from labrat.agent.tools.base import Tool, ToolContext
from labrat.history.log import QueryHistoryLog
from labrat.history.search import find_queries_touching, find_similar_queries, recent_queries


class _Input(BaseModel):
    keyword: str | None = Field(
        default=None,
        description="Optional keyword or partial SQL to search for in past queries.",
    )
    table: str | None = Field(
        default=None,
        description="Optional table name to filter — returns queries that touched this table.",
    )
    limit: int = Field(default=10, description="Maximum number of results to return.")


class SearchQueryHistoryTool(Tool[_Input]):
    """Search past query history to inform new queries.

    Returns relevant past SQL queries ranked by recency and similarity.
    Use this to re-use proven patterns, recall common filters, or check
    what joins have been used before.
    """

    def __init__(self, history_log: QueryHistoryLog | None = None) -> None:
        self._log = history_log or QueryHistoryLog()

    @property
    def name(self) -> str:
        return "search_query_history"

    @property
    def description(self) -> str:
        return (
            "Search past SQL queries for the active profile. "
            "Returns ranked past queries by keyword similarity or table name. "
            "Useful for re-using proven patterns and common joins."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> object:
        events = self._log.read_profile(ctx.profile_name)
        if not events:
            return {"queries": [], "message": "No query history found for this profile."}

        if args.table:
            results = find_queries_touching(events, table=args.table)
        elif args.keyword:
            results = find_similar_queries(events, sql=args.keyword, limit=args.limit)
        else:
            results = recent_queries(events, limit=args.limit)

        results = results[: args.limit]
        return {
            "queries": [
                {
                    "sql": e.sql_final,
                    "timestamp": e.timestamp.isoformat(),
                    "executed": e.executed,
                    "success": e.success,
                    "row_count": e.row_count,
                }
                for e in results
            ]
        }

    def anthropic_schema(self) -> dict[str, Any]:
        return super().anthropic_schema()
