"""search_columns tool: find columns matching a keyword across all tables."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, Field

from labrat.agent.tools.base import Tool, ToolContext
from labrat.db.catalog import Catalog


class _Input(BaseModel):
    keyword: str
    database: str | None = Field(
        default=None,
        description="Connection name when multiple databases are available; defaults to primary.",
    )


class _ColumnMatch(BaseModel):
    schema_name: str
    table_name: str
    column_name: str
    data_type: str
    nullable: bool


class _Output(BaseModel):
    keyword: str
    matches: list[_ColumnMatch]


class SearchColumnsTool(Tool[_Input]):
    """Search all tables for columns whose name contains the given keyword."""

    @property
    def name(self) -> str:
        return "search_columns"

    @property
    def description(self) -> str:
        return (
            "Search across all tables for columns whose name contains a keyword. "
            "Use this when you're looking for a specific concept (e.g. 'revenue', 'user', 'date') "
            "and don't know which table or column name to use."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        catalog = cast(Catalog, ctx.catalogs[args.database or ctx.primary])
        needle = args.keyword.lower()
        matches: list[_ColumnMatch] = []
        for schema in catalog.schemas:
            for table in schema.tables:
                for col in table.columns:
                    if needle in col.name.lower():
                        matches.append(
                            _ColumnMatch(
                                schema_name=schema.name,
                                table_name=table.name,
                                column_name=col.name,
                                data_type=col.data_type,
                                nullable=col.nullable,
                            )
                        )
        return _Output(keyword=args.keyword, matches=matches)
