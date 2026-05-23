"""DuckDB connection implementation."""

from pathlib import Path

import duckdb
import polars as pl

from labrat.db.base import Connection
from labrat.db.catalog import Catalog, Column, ColumnStats, ForeignKey, Schema, Table


class DuckDBConnection(Connection):
    """DuckDB implementation of the Connection interface.

    Supports both file-based databases and in-memory databases (path=":memory:").
    Returns Polars DataFrames via Arrow zero-copy where supported.
    """

    def __init__(self, path: str | Path = ":memory:", read_only: bool = True) -> None:
        self._path = str(path)
        self._read_only = read_only
        self._conn: duckdb.DuckDBPyConnection | None = None

    @property
    def _connection(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            raise RuntimeError("Not connected; call connect() first.")
        return self._conn

    def connect(self) -> None:
        self._conn = duckdb.connect(self._path, read_only=self._read_only)

    def disconnect(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def execute(self, sql: str) -> pl.DataFrame:
        """Execute SQL and return a Polars DataFrame."""
        result: pl.DataFrame = pl.read_database(  # pyright: ignore[reportUnknownMemberType]
            sql, connection=self._connection
        )
        return result

    def explain(self, sql: str) -> str:
        """Return the DuckDB EXPLAIN output for the query."""
        result = self._connection.execute(f"EXPLAIN {sql}").fetchall()
        return "\n".join(str(row[1]) for row in result if row)

    def sample_table(self, table: str, n: int = 10) -> pl.DataFrame:
        return self.execute(f"SELECT * FROM {table} USING SAMPLE {n}")

    def column_stats(self, table: str, column: str) -> ColumnStats:
        """Return min/max/null_count/distinct_count/dtype for a column."""
        type_row = self._connection.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = ? AND column_name = ?",
            [table.split(".")[-1], column],
        ).fetchone()
        data_type = str(type_row[0]) if type_row else "UNKNOWN"

        stats_sql = f"""
            SELECT
                COUNT(*) FILTER (WHERE {column} IS NULL)   AS null_count,
                COUNT(DISTINCT {column})                    AS distinct_count,
                MIN(CAST({column} AS VARCHAR))              AS min_value,
                MAX(CAST({column} AS VARCHAR))              AS max_value
            FROM {table}
        """
        row = self._connection.execute(stats_sql).fetchone()
        null_count, distinct_count, min_val, max_val = (0, 0, None, None) if row is None else row

        return ColumnStats(
            column_name=column,
            table_name=table,
            data_type=data_type,
            null_count=int(null_count),
            distinct_count=int(distinct_count),
            min_value=str(min_val) if min_val is not None else None,
            max_value=str(max_val) if max_val is not None else None,
        )

    def introspect_catalog(self) -> Catalog:
        """Introspect the full DuckDB schema."""
        db_name_row = self._connection.execute("SELECT current_database()").fetchone()
        db_name = str(db_name_row[0]) if db_name_row else "unknown"

        schemas: list[Schema] = []
        schema_rows = self._connection.execute(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name NOT IN ('information_schema', 'pg_catalog') "
            "ORDER BY schema_name"
        ).fetchall()

        for (schema_name,) in schema_rows:
            tables = self._introspect_schema(str(schema_name))
            schemas.append(Schema(name=str(schema_name), tables=tables))

        return Catalog(database_name=db_name, schemas=schemas)

    def _introspect_schema(self, schema_name: str) -> list[Table]:
        table_rows = self._connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = ? AND table_type = 'BASE TABLE' "
            "ORDER BY table_name",
            [schema_name],
        ).fetchall()

        tables: list[Table] = []
        for (table_name,) in table_rows:
            columns = self._introspect_columns(schema_name, str(table_name))
            fks = self._introspect_fks(schema_name, str(table_name))
            tables.append(
                Table(
                    name=str(table_name),
                    schema_name=schema_name,
                    columns=columns,
                    foreign_keys=fks,
                )
            )
        return tables

    def _introspect_columns(self, schema_name: str, table_name: str) -> list[Column]:
        rows = self._connection.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ? "
            "ORDER BY ordinal_position",
            [schema_name, table_name],
        ).fetchall()
        return [
            Column(
                name=str(col_name),
                data_type=str(data_type),
                nullable=str(is_nullable).upper() == "YES",
                default=str(col_default) if col_default is not None else None,
            )
            for col_name, data_type, is_nullable, col_default in rows
        ]

    def _introspect_fks(self, schema_name: str, table_name: str) -> list[ForeignKey]:
        # DuckDB doesn't enforce FK constraints, but we can check if columns
        # follow FK naming conventions (e.g. region_id -> regions.id).
        # Return empty list for now; real FK detection requires PRAGMA or metadata.
        _ = schema_name, table_name
        return []
