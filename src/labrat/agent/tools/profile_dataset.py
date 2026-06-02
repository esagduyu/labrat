"""profile_dataset tool: one-call upfront profile of a database for grounding.

This is LabRat's "Data Analyzer" / Scent generator (north-star Pillar 1). Instead of
making the model call list_tables → describe_table → sample_rows table by table,
``profile_dataset`` returns a compact, size-budgeted snapshot of a whole connection —
schema, row counts, declared foreign keys, and a few sample rows per table — so the
agent is grounded in the real data before it plans. Its output is also the raw material
the future Rat Maze promotes into Scent (semantic grounding).

Reads table structure from the introspected catalog (``ctx.catalogs[db]``) and samples
live rows from the connection. Requires the catalog to be populated — in the DAB
labrat-agent driver that happens via ``introspect_env_catalogs`` at trial start.
"""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, Field

from labrat.agent.tools.base import Tool, ToolContext
from labrat.db.base import Connection
from labrat.db.catalog import Catalog, Table


class _Input(BaseModel):
    database: str | None = Field(
        default=None,
        description="Connection name when multiple databases are available; defaults to primary.",
    )
    tables: list[str] | None = Field(
        default=None,
        description="Restrict the profile to these table names; default profiles all tables.",
    )
    sample_rows: int = Field(
        default=3,
        ge=0,
        le=20,
        description="Rows to sample per table (0 to skip sampling).",
    )
    max_tables: int = Field(
        default=40,
        ge=1,
        description="Cap on tables profiled; if exceeded the result is truncated and flagged.",
    )


class _ColumnInfo(BaseModel):
    name: str
    data_type: str
    nullable: bool


class _TableProfile(BaseModel):
    name: str
    schema_name: str
    row_count: int | None = None
    columns: list[_ColumnInfo]
    foreign_keys: list[str] = []  # "column -> referenced_table.referenced_column"
    sample_columns: list[str] = []
    sample: list[list[str]] = []
    note: str | None = None


class _Output(BaseModel):
    database: str
    tables_total: int
    tables_profiled: int
    truncated: bool = False
    note: str | None = None
    tables: list[_TableProfile] = []


class ProfileDatasetTool(Tool[_Input]):
    """Profile an entire database in one call to ground the agent before it plans."""

    @property
    def name(self) -> str:
        return "profile_dataset"

    @property
    def description(self) -> str:
        return (
            "Profile a whole database in one call: for every table, its columns and "
            "types, row count, declared foreign keys, and a few sample rows. "
            "Call this FIRST to understand the data before planning or writing SQL, "
            "instead of describing tables one at a time. Use the `tables` argument to "
            "focus on specific tables, or `sample_rows=0` to skip row sampling."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        db = args.database or ctx.primary
        catalog = cast(Catalog, ctx.catalogs[db])
        conn = cast(Connection, ctx.connections[db])

        all_tables = [t for schema in catalog.schemas for t in schema.tables]
        if args.tables is not None:
            wanted = set(args.tables)
            all_tables = [t for t in all_tables if t.name in wanted]

        tables_total = len(all_tables)
        selected = all_tables[: args.max_tables]
        truncated = tables_total > len(selected)

        profiles = [self._profile_table(conn, t, args.sample_rows) for t in selected]

        note: str | None = None
        if tables_total == 0:
            note = (
                "No tables found. The catalog may not be introspected, or `tables` "
                "matched nothing — try list_tables."
            )
        elif truncated:
            note = (
                f"Showing {len(selected)} of {tables_total} tables (max_tables="
                f"{args.max_tables}). Pass `tables` or raise `max_tables` for more."
            )

        return _Output(
            database=db,
            tables_total=tables_total,
            tables_profiled=len(profiles),
            truncated=truncated,
            note=note,
            tables=profiles,
        )

    def _profile_table(self, conn: Connection, table: Table, sample_rows: int) -> _TableProfile:
        columns = [
            _ColumnInfo(name=c.name, data_type=c.data_type, nullable=c.nullable)
            for c in table.columns
        ]
        fks = [
            f"{fk.column} -> {fk.referenced_table}.{fk.referenced_column}"
            for fk in table.foreign_keys
        ]

        row_count = table.row_count
        sample_columns: list[str] = []
        sample: list[list[str]] = []
        note: str | None = None

        if sample_rows > 0:
            try:
                df = conn.sample_table(table.qualified_name, n=sample_rows)
                sample_columns = df.columns
                sample = [[str(v) if v is not None else "" for v in row] for row in df.iter_rows()]
            except Exception as exc:
                note = f"sampling failed: {exc}"

        if row_count is None:
            try:
                count_df = conn.execute(f"SELECT COUNT(*) AS n FROM {table.qualified_name}")
                if len(count_df) > 0:
                    row_count = int(count_df.item(0, 0))
            except Exception:
                pass  # leave row_count unknown rather than fail the profile

        return _TableProfile(
            name=table.name,
            schema_name=table.schema_name,
            row_count=row_count,
            columns=columns,
            foreign_keys=fks,
            sample_columns=sample_columns,
            sample=sample,
            note=note,
        )
