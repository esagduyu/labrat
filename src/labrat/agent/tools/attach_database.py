"""attach_database tool: ATTACH a SQLite/Postgres/MySQL database into the DuckDB session.

Enables cross-database JOINs in a single connection — e.g., when a dataset stores
some tables in DuckDB and others in SQLite, ATTACH makes the SQLite tables
addressable as ``alias.table_name`` from the primary DuckDB connection.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from labrat.agent.tools.base import Tool, ToolContext
from labrat.db.duckdb_engine import DuckDBConnection


class _Input(BaseModel):
    path: str = Field(
        description="Filesystem path (SQLite) or connection string (Postgres/MySQL).",
    )
    alias: str = Field(
        description="Short SQL identifier used to reference attached tables (e.g. 'ext').",
    )
    db_type: Literal["sqlite", "postgres", "mysql", "duckdb"] = Field(
        description="Database type of the file being attached.",
    )
    database: str | None = Field(
        default=None,
        description="Primary connection name to attach into; defaults to primary.",
    )


class _Output(BaseModel):
    ok: bool
    alias: str
    message: str


class AttachDatabaseTool(Tool[_Input]):
    """ATTACH another database into the primary DuckDB session for cross-DB JOINs."""

    mutating = True

    @property
    def name(self) -> str:
        return "attach_database"

    @property
    def description(self) -> str:
        return (
            "Attach a SQLite/Postgres/MySQL database into the primary DuckDB session. "
            "After attach, tables in the attached database can be referenced as "
            "`alias.table_name` from any subsequent run_sql call against the primary "
            "connection. Use this to JOIN across databases without leaving DuckDB."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        conn = ctx.connections[args.database or ctx.primary]
        if not isinstance(conn, DuckDBConnection):
            return _Output(
                ok=False,
                alias=args.alias,
                message=(
                    f"attach_database requires a DuckDB primary connection; "
                    f"got {type(conn).__name__}."
                ),
            )
        try:
            conn.attach(args.path, args.alias, args.db_type)
        except Exception as exc:
            return _Output(ok=False, alias=args.alias, message=f"ATTACH failed: {exc}")

        catalog_note = ""
        try:
            ctx.catalogs[args.alias] = conn.introspect_attached_catalog(args.alias)
            # Register the alias against the SAME primary connection so catalog tools
            # that need a live handle (column_stats, describe_table value-hints) resolve
            # it; attached tables are addressed as alias.table through this connection.
            ctx.connections[args.alias] = conn
        except Exception as exc:  # introspection is best-effort; never fail the attach
            catalog_note = f" (schema not indexed: {exc})"
        return _Output(
            ok=True,
            alias=args.alias,
            message=(
                f"Attached {args.path!r} as {args.alias}. "
                f"Reference its tables as {args.alias}.<table_name>.{catalog_note}"
            ),
        )
