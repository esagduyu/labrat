"""load_file tool: load a CSV/TSV/JSON/Parquet file into the DuckDB session.

Widens "connect & ingest" (north-star Pillar 1) beyond pre-wired databases: the agent
can pull a flat file into the primary DuckDB session as a TEMP table and then query it
with run_sql / join it against existing tables. Uses DuckDB's native auto-readers, so —
like attach_database — it requires a DuckDB primary connection.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from labrat.agent.tools.base import Tool, ToolContext
from labrat.db.duckdb_engine import DuckDBConnection


class _Input(BaseModel):
    path: str = Field(description="Filesystem path to the CSV/TSV/JSON/Parquet file.")
    table_name: str = Field(
        description="Name for the loaded table (alphanumeric/underscore); query it via run_sql.",
    )
    file_format: Literal["csv", "tsv", "json", "jsonl", "ndjson", "parquet", "pq"] | None = Field(
        default=None,
        description="File format; inferred from the path extension when omitted.",
    )
    database: str | None = Field(
        default=None,
        description="Primary connection name to load into; defaults to primary.",
    )


class _Output(BaseModel):
    ok: bool
    table_name: str
    row_count: int | None = None
    message: str


class LoadFileTool(Tool[_Input]):
    """Load a CSV/TSV/JSON/Parquet file into the DuckDB session as a queryable table."""

    mutating = True

    @property
    def name(self) -> str:
        return "load_file"

    @property
    def description(self) -> str:
        return (
            "Load a CSV, TSV, JSON, or Parquet file into the primary DuckDB session as a "
            "table you can then query with run_sql or JOIN against existing tables. "
            "Format is inferred from the file extension unless you pass file_format."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        conn = ctx.connections[args.database or ctx.primary]
        if not isinstance(conn, DuckDBConnection):
            return _Output(
                ok=False,
                table_name=args.table_name,
                message=(
                    f"load_file requires a DuckDB primary connection; got {type(conn).__name__}."
                ),
            )
        try:
            row_count = conn.load_file(args.path, args.table_name, args.file_format)
        except Exception as exc:
            return _Output(ok=False, table_name=args.table_name, message=f"load_file failed: {exc}")
        return _Output(
            ok=True,
            table_name=args.table_name,
            row_count=row_count,
            message=(
                f"Loaded {row_count} rows from {args.path!r} into {args.table_name}. "
                f"Query it with run_sql against the primary connection."
            ),
        )
