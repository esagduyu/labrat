"""load_mongo_collection tool: fetch a MongoDB collection into a DuckDB temp table.

MongoDB isn't ATTACH-able by DuckDB. This tool materializes a Mongo find()
result as a DuckDB table on the primary connection — the agent then queries it
with ``run_sql`` like any other table.

Mongo connection URL comes from the ``LABRAT_MCP_MONGO_URL`` env var (default
``mongodb://localhost:27017``, matching the DAB local setup). Nested document
fields land as DuckDB STRUCTs; the agent uses dot notation to address them.
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field

from labrat.agent.tools.base import Tool, ToolContext
from labrat.db.duckdb_engine import DuckDBConnection


class _Input(BaseModel):
    database: str = Field(description="MongoDB database name (e.g. 'articles_db').")
    collection: str = Field(description="MongoDB collection name within the database.")
    target_table: str = Field(
        description=(
            "DuckDB table name to materialize into on the primary connection. "
            "Replaces any existing table by that name. Must be alphanumeric + underscores."
        ),
    )
    filter: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional Mongo find() filter (JSON object). Empty = full collection.",
    )
    limit: int | None = Field(
        default=None,
        description="Optional row cap. Useful for huge collections; omit for full load.",
    )
    primary_db: str | None = Field(
        default=None,
        description="Connection name when multiple databases are available; defaults to primary.",
    )


class _Output(BaseModel):
    ok: bool
    table: str
    rows: int
    message: str


class LoadMongoCollectionTool(Tool[_Input]):
    """Pull a MongoDB collection into a DuckDB table for SQL querying."""

    mutating = True

    @property
    def name(self) -> str:
        return "load_mongo_collection"

    @property
    def description(self) -> str:
        return (
            "Materialize a MongoDB collection (or a filtered subset) into a DuckDB "
            "table on the primary connection so it can be JOIN-ed, aggregated, or "
            "filtered with run_sql. Nested document fields become DuckDB STRUCTs — "
            "address them with dot notation (e.g. `doc.author.name`). Connection URL "
            "is read from LABRAT_MCP_MONGO_URL (default mongodb://localhost:27017)."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        try:
            import polars as pl
            import pymongo
        except ImportError as exc:
            return _Output(
                ok=False,
                table=args.target_table,
                rows=0,
                message=f"load_mongo_collection requires pymongo + polars: {exc}",
            )

        conn = ctx.connections[args.primary_db or ctx.primary]
        if not isinstance(conn, DuckDBConnection):
            return _Output(
                ok=False,
                table=args.target_table,
                rows=0,
                message=(
                    f"load_mongo_collection requires a DuckDB primary; got {type(conn).__name__}."
                ),
            )

        mongo_url = os.environ.get("LABRAT_MCP_MONGO_URL", "mongodb://localhost:27017")
        try:
            client: pymongo.MongoClient[dict[str, Any]] = pymongo.MongoClient(mongo_url)
            try:
                cursor = client[args.database][args.collection].find(args.filter)
                if args.limit is not None:
                    cursor = cursor.limit(args.limit)
                docs: list[dict[str, Any]] = list(cursor)
            finally:
                client.close()
        except Exception as exc:
            return _Output(
                ok=False,
                table=args.target_table,
                rows=0,
                message=f"MongoDB query failed: {exc}",
            )

        # ObjectId isn't natively JSON/Arrow-friendly; stringify so polars can ingest.
        for d in docs:
            if "_id" in d:
                d["_id"] = str(d["_id"])

        if not docs:
            # Create an empty table so the agent can still describe / query without
            # an "unknown table" error.
            conn.execute(f"CREATE OR REPLACE TABLE {args.target_table} (placeholder VARCHAR)")
            return _Output(
                ok=True,
                table=args.target_table,
                rows=0,
                message=(
                    f"Collection {args.database}.{args.collection} returned 0 docs "
                    f"for the given filter; created empty table {args.target_table}."
                ),
            )

        try:
            df = pl.DataFrame(docs, infer_schema_length=min(len(docs), 1000))
        except Exception as exc:
            return _Output(
                ok=False,
                table=args.target_table,
                rows=0,
                message=f"Failed to convert {len(docs)} docs to polars DataFrame: {exc}",
            )

        try:
            conn.materialize_table(args.target_table, df.to_arrow())  # type: ignore[arg-type]
        except Exception as exc:
            return _Output(
                ok=False,
                table=args.target_table,
                rows=0,
                message=f"Failed to materialize into DuckDB: {exc}",
            )

        return _Output(
            ok=True,
            table=args.target_table,
            rows=len(docs),
            message=(
                f"Materialized {len(docs)} docs from {args.database}.{args.collection} "
                f"into DuckDB table {args.target_table}. Query with run_sql; nested "
                f"fields use dot notation (e.g. {args.target_table}.field.subfield)."
            ),
        )
