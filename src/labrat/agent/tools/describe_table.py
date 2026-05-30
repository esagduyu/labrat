"""describe_table tool: return schema detail for a single table."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, Field

from labrat.agent.tools.base import Tool, ToolContext
from labrat.db.catalog import Catalog


class _Input(BaseModel):
    table: str
    database: str | None = Field(
        default=None,
        description="Connection name when multiple databases are available; defaults to primary.",
    )


class _ColumnDetail(BaseModel):
    name: str
    data_type: str
    nullable: bool
    default: str | None = None


class _FKDetail(BaseModel):
    column: str
    references: str  # "table.column"


class _Output(BaseModel):
    table_name: str
    schema_name: str
    columns: list[_ColumnDetail]
    foreign_keys: list[_FKDetail]
    row_count: int | None = None


class DescribeTableTool(Tool[_Input]):
    """Describe a table's columns, types, nullability, and foreign keys."""

    @property
    def name(self) -> str:
        return "describe_table"

    @property
    def description(self) -> str:
        return (
            "Return the full schema of a table: column names, data types, "
            "nullability, defaults, and foreign key relationships. "
            "Use this before writing a query to understand the table structure."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        catalog = cast(Catalog, ctx.catalogs[args.database or ctx.primary])
        table = catalog.find_table(args.table)
        if table is None:
            raise ValueError(f"Table {args.table!r} not found in catalog")

        columns = [
            _ColumnDetail(
                name=col.name,
                data_type=col.data_type,
                nullable=col.nullable,
                default=col.default,
            )
            for col in table.columns
        ]
        fks = [
            _FKDetail(
                column=fk.column,
                references=f"{fk.referenced_table}.{fk.referenced_column}",
            )
            for fk in table.foreign_keys
        ]
        return _Output(
            table_name=table.name,
            schema_name=table.schema_name,
            columns=columns,
            foreign_keys=fks,
            row_count=table.row_count,
        )
