"""Redshift connection adapter (M25).

Uses redshift-connector. Install: uv add "redshift-connector"
"""

from __future__ import annotations

from typing import Any

import polars as pl

from labrat.db.base import Connection
from labrat.db.catalog import Catalog, Column, ColumnStats, Schema, Table


class RedshiftConnection(Connection):
    """Amazon Redshift connection using redshift-connector."""

    def __init__(
        self,
        host: str,
        database: str = "dev",
        user: str = "",
        password: str = "",
        port: int = 5439,
    ) -> None:
        self._host = host
        self._database = database
        self._user = user
        self._password = password
        self._port = port
        self._conn: Any = None

    def __repr__(self) -> str:
        status = "connected" if self._conn is not None else "disconnected"
        return f"RedshiftConnection(host={self._host!r}, {status})"

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def connect(self) -> None:
        import redshift_connector  # type: ignore[import-untyped]

        self._conn = redshift_connector.connect(  # pyright: ignore[reportUnknownMemberType]
            host=self._host,
            database=self._database,
            user=self._user,
            password=self._password,
            port=self._port,
        )

    def disconnect(self) -> None:
        if self._conn is not None:
            self._conn.close()  # pyright: ignore[reportUnknownMemberType]
            self._conn = None

    # ── query execution ───────────────────────────────────────────────────────

    def execute(self, sql: str) -> pl.DataFrame:
        if self._conn is None:
            raise RuntimeError("Not connected.")
        with self._conn.cursor() as cur:  # pyright: ignore[reportUnknownMemberType]
            cur.execute(sql)  # pyright: ignore[reportUnknownMemberType]
            rows = cur.fetchall()  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
            col_names: list[str] = [desc[0] for desc in cur.description]  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
        return pl.DataFrame(rows, schema=col_names, orient="row")  # pyright: ignore[reportUnknownArgumentType]

    def explain(self, sql: str) -> str:
        df = self.execute(f"EXPLAIN {sql}")
        return "\n".join(str(row[df.columns[0]]) for row in df.to_dicts())  # pyright: ignore[reportUnknownMemberType]

    def sample_table(self, table: str, n: int = 10) -> pl.DataFrame:
        return self.execute(f"SELECT * FROM {table} LIMIT {n}")

    # ── catalog introspection ─────────────────────────────────────────────────

    def introspect_catalog(self) -> Catalog:
        if self._conn is None:
            raise RuntimeError("Not connected.")
        sql = """
            SELECT table_schema, table_name, column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema', 'pg_internal')
            ORDER BY table_schema, table_name, ordinal_position
        """
        df = self.execute(sql)
        rows: list[dict[str, Any]] = df.to_dicts()  # pyright: ignore[reportUnknownMemberType]

        grouped: dict[tuple[str, str], list[Column]] = {}
        for row in rows:
            key = (str(row["table_schema"]), str(row["table_name"]))
            col = Column(
                name=str(row["column_name"]),
                data_type=str(row["data_type"]),
                nullable=str(row["is_nullable"]).upper() == "YES",
            )
            grouped.setdefault(key, []).append(col)

        schema_map: dict[str, list[Table]] = {}
        for (schema_name, table_name), cols in grouped.items():
            table = Table(schema_name=schema_name, name=table_name, columns=cols)
            schema_map.setdefault(schema_name, []).append(table)

        schemas = [Schema(name=n, tables=tbls) for n, tbls in schema_map.items()]
        return Catalog(database_name=self._database, schemas=schemas)

    # ── column statistics ─────────────────────────────────────────────────────

    def column_stats(self, table: str, column: str) -> ColumnStats:
        sql = f"""
            SELECT
                SUM(CASE WHEN {column} IS NULL THEN 1 ELSE 0 END) AS null_count,
                COUNT(DISTINCT {column})                            AS distinct_count,
                MIN({column}::varchar)                             AS min_value,
                MAX({column}::varchar)                             AS max_value
            FROM {table}
        """
        df = self.execute(sql)
        row = df.row(0)  # pyright: ignore[reportUnknownMemberType]
        return ColumnStats(
            column_name=column,
            table_name=table,
            data_type="unknown",
            null_count=int(row[0] or 0),
            distinct_count=int(row[1] or 0),
            min_value=str(row[2]) if row[2] is not None else None,
            max_value=str(row[3]) if row[3] is not None else None,
        )
