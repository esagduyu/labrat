"""list_tables tool: return all tables in the catalog."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel

from labrat.agent.tools.base import Tool, ToolContext
from labrat.db.catalog import Catalog


class _Input(BaseModel):
    schema_filter: str | None = None


class _TableSummary(BaseModel):
    name: str
    schema_name: str
    column_count: int
    row_count: int | None = None


class _Output(BaseModel):
    tables: list[_TableSummary]
    schema_filter: str | None = None


class ListTablesTool(Tool[_Input]):
    """List all tables visible in the database catalog."""

    @property
    def name(self) -> str:
        return "list_tables"

    @property
    def description(self) -> str:
        return (
            "List all tables available in the database. "
            "Optionally filter by schema name. "
            "Use this to discover what tables exist before querying."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        catalog = cast(Catalog, ctx.catalog)
        tables: list[_TableSummary] = []
        for schema in catalog.schemas:
            if args.schema_filter and schema.name != args.schema_filter:
                continue
            for table in schema.tables:
                tables.append(
                    _TableSummary(
                        name=table.name,
                        schema_name=schema.name,
                        column_count=len(table.columns),
                        row_count=table.row_count,
                    )
                )
        return _Output(tables=tables, schema_filter=args.schema_filter)
