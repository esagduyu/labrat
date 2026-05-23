"""PostgreSQL connection adapter (M23).

Uses psycopg (v3) with binary protocol for Arrow-compatible transfers.
psycopg must be installed: uv add "psycopg[binary]"
"""

from __future__ import annotations

from typing import Any

import polars as pl

from labrat.db.base import Connection
from labrat.db.catalog import Catalog, Column, ColumnStats, Schema, Table


class PostgresConnection(Connection):
    """PostgreSQL connection using psycopg v3."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: Any = None

    def __repr__(self) -> str:
        status = "connected" if self._conn is not None else "disconnected"
        return f"PostgresConnection({status})"

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def connect(self) -> None:
        import psycopg  # type: ignore[import-untyped]

        self._conn = psycopg.connect(self._dsn)  # pyright: ignore[reportUnknownMemberType]

    def disconnect(self) -> None:
        if self._conn is not None:
            self._conn.close()  # pyright: ignore[reportUnknownMemberType]
            self._conn = None

    # ── query execution ───────────────────────────────────────────────────────

    def execute(self, sql: str) -> pl.DataFrame:
        if self._conn is None:
            raise RuntimeError("Not connected. Call connect() first.")
        with self._conn.cursor() as cur:  # pyright: ignore[reportUnknownMemberType]
            cur.execute(sql)  # pyright: ignore[reportUnknownMemberType]
            rows = cur.fetchall()  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
            col_names: list[str] = [desc[0] for desc in cur.description]  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
        return pl.DataFrame(rows, schema=col_names, orient="row")  # pyright: ignore[reportUnknownArgumentType]

    def explain(self, sql: str) -> str:
        if self._conn is None:
            raise RuntimeError("Not connected. Call connect() first.")
        with self._conn.cursor() as cur:  # pyright: ignore[reportUnknownMemberType]
            cur.execute(f"EXPLAIN {sql}")  # pyright: ignore[reportUnknownMemberType]
            rows = cur.fetchall()  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
        return "\n".join(row[0] for row in rows)  # pyright: ignore[reportUnknownVariableType]

    def sample_table(self, table: str, n: int = 10) -> pl.DataFrame:
        return self.execute(f"SELECT * FROM {table} LIMIT {n}")

    # ── catalog introspection ─────────────────────────────────────────────────

    def introspect_catalog(self) -> Catalog:
        if self._conn is None:
            raise RuntimeError("Not connected. Call connect() first.")

        columns = self._fetch_columns()
        tables = self._build_tables(columns)
        schemas = self._group_schemas(tables)
        db_name = self._fetch_db_name()
        return Catalog(database_name=db_name, schemas=schemas)

    def _fetch_db_name(self) -> str:
        df = self.execute("SELECT current_database()")
        return str(df[df.columns[0]][0])

    def _fetch_columns(self) -> list[dict[str, Any]]:
        sql = """
            SELECT
                table_schema,
                table_name,
                column_name,
                data_type,
                is_nullable
            FROM information_schema.columns
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY table_schema, table_name, ordinal_position
        """
        df = self.execute(sql)
        return df.to_dicts()  # pyright: ignore[reportUnknownMemberType]

    def _build_tables(self, column_rows: list[dict[str, Any]]) -> list[Table]:
        grouped: dict[tuple[str, str], list[Column]] = {}
        for row in column_rows:
            key = (str(row["table_schema"]), str(row["table_name"]))
            col = Column(
                name=str(row["column_name"]),
                data_type=str(row["data_type"]),
                nullable=str(row["is_nullable"]).upper() == "YES",
            )
            grouped.setdefault(key, []).append(col)

        return [
            Table(schema_name=schema, name=tname, columns=cols)
            for (schema, tname), cols in grouped.items()
        ]

    def _group_schemas(self, tables: list[Table]) -> list[Schema]:
        schema_map: dict[str, list[Table]] = {}
        for table in tables:
            schema_map.setdefault(table.schema_name, []).append(table)
        return [Schema(name=name, tables=tbls) for name, tbls in schema_map.items()]

    # ── column statistics ─────────────────────────────────────────────────────

    def column_stats(self, table: str, column: str) -> ColumnStats:
        sql = f"""
            SELECT
                COUNT(*) FILTER (WHERE {column} IS NULL) AS null_count,
                COUNT(DISTINCT {column})                  AS distinct_count,
                MIN({column}::text)                       AS min_value,
                MAX({column}::text)                       AS max_value
            FROM {table}
        """
        df = self.execute(sql)
        row = df.row(0)  # pyright: ignore[reportUnknownMemberType]

        # Fetch type from information_schema
        schema_part, table_part = table.split(".", 1) if "." in table else ("public", table)
        type_df = self.execute(
            f"""
            SELECT data_type FROM information_schema.columns
            WHERE table_schema = '{schema_part}'
              AND table_name   = '{table_part}'
              AND column_name  = '{column}'
            LIMIT 1
            """
        )
        data_type = str(type_df[type_df.columns[0]][0]) if len(type_df) > 0 else "unknown"

        return ColumnStats(
            column_name=column,
            table_name=table,
            data_type=data_type,
            null_count=int(row[0]),
            distinct_count=int(row[1]),
            min_value=str(row[2]) if row[2] is not None else None,
            max_value=str(row[3]) if row[3] is not None else None,
        )
