"""MySQL connection adapter (M25).

Uses PyMySQL. Install: uv add "pymysql"
"""

from __future__ import annotations

from typing import Any

import polars as pl

from labrat.db.base import Connection
from labrat.db.catalog import Catalog, Column, ColumnStats, Schema, Table


class MySQLConnection(Connection):
    """MySQL connection using PyMySQL."""

    def __init__(
        self,
        host: str,
        database: str = "",
        user: str = "",
        password: str = "",
        port: int = 3306,
        charset: str = "utf8mb4",
    ) -> None:
        self._host = host
        self._database = database
        self._user = user
        self._password = password
        self._port = port
        self._charset = charset
        self._conn: Any = None

    def __repr__(self) -> str:
        status = "connected" if self._conn is not None else "disconnected"
        return f"MySQLConnection(host={self._host!r}, {status})"

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def connect(self) -> None:
        import pymysql  # type: ignore[import-untyped]

        self._conn = pymysql.connect(  # pyright: ignore[reportUnknownMemberType]
            host=self._host,
            user=self._user,
            password=self._password,
            database=self._database,
            port=self._port,
            charset=self._charset,
            cursorclass=pymysql.cursors.DictCursor,  # pyright: ignore[reportUnknownMemberType,reportAttributeAccessIssue]
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
            rows: list[dict[str, Any]] = list(cur.fetchall())  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
        if not rows:
            return pl.DataFrame()
        return pl.from_dicts(rows)  # pyright: ignore[reportUnknownArgumentType]

    def explain(self, sql: str) -> str:
        df = self.execute(f"EXPLAIN {sql}")
        return "\n".join(str(row) for row in df.to_dicts())  # pyright: ignore[reportUnknownMemberType]

    def sample_table(self, table: str, n: int = 10) -> pl.DataFrame:
        return self.execute(f"SELECT * FROM {table} LIMIT {n}")

    # ── catalog introspection ─────────────────────────────────────────────────

    def introspect_catalog(self) -> Catalog:
        if self._conn is None:
            raise RuntimeError("Not connected.")
        sql = f"""
            SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = '{self._database}'
            ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
        """
        df = self.execute(sql)
        rows: list[dict[str, Any]] = df.to_dicts()  # pyright: ignore[reportUnknownMemberType]

        grouped: dict[tuple[str, str], list[Column]] = {}
        for row in rows:
            key = (str(row["TABLE_SCHEMA"]), str(row["TABLE_NAME"]))
            col = Column(
                name=str(row["COLUMN_NAME"]),
                data_type=str(row["DATA_TYPE"]),
                nullable=str(row["IS_NULLABLE"]).upper() == "YES",
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
                MIN(CAST({column} AS CHAR))                        AS min_value,
                MAX(CAST({column} AS CHAR))                        AS max_value
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
