"""Snowflake connection adapter (M25).

Uses snowflake-connector-python. Install: uv add "snowflake-connector-python"
"""

from __future__ import annotations

from typing import Any

import polars as pl

from labrat.db.base import Connection
from labrat.db.catalog import Catalog, Column, ColumnStats, Schema, Table


class SnowflakeConnection(Connection):
    """Snowflake connection using snowflake-connector-python."""

    def __init__(
        self,
        account: str,
        user: str,
        password: str = "",
        database: str | None = None,
        schema: str | None = None,
        warehouse: str | None = None,
        role: str | None = None,
    ) -> None:
        self._account = account
        self._user = user
        self._password = password
        self._database = database
        self._schema = schema
        self._warehouse = warehouse
        self._role = role
        self._conn: Any = None

    def __repr__(self) -> str:
        status = "connected" if self._conn is not None else "disconnected"
        return f"SnowflakeConnection(account={self._account!r}, {status})"

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def connect(self) -> None:
        import snowflake.connector  # type: ignore[import-untyped]

        kwargs: dict[str, Any] = {
            "account": self._account,
            "user": self._user,
            "password": self._password,
        }
        if self._database:
            kwargs["database"] = self._database
        if self._schema:
            kwargs["schema"] = self._schema
        if self._warehouse:
            kwargs["warehouse"] = self._warehouse
        if self._role:
            kwargs["role"] = self._role
        self._conn = snowflake.connector.connect(**kwargs)  # pyright: ignore[reportUnknownMemberType]

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
        return "\n".join(str(row) for row in df.to_dicts())  # pyright: ignore[reportUnknownMemberType]

    def sample_table(self, table: str, n: int = 10) -> pl.DataFrame:
        return self.execute(f"SELECT * FROM {table} LIMIT {n}")

    # ── catalog introspection ─────────────────────────────────────────────────

    def introspect_catalog(self) -> Catalog:
        if self._conn is None:
            raise RuntimeError("Not connected.")
        sql = """
            SELECT table_schema, table_name, column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema NOT IN ('INFORMATION_SCHEMA')
            ORDER BY table_schema, table_name, ordinal_position
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
        db_name = self._database or "snowflake"
        return Catalog(database_name=db_name, schemas=schemas)

    # ── column statistics ─────────────────────────────────────────────────────

    def column_stats(self, table: str, column: str) -> ColumnStats:
        sql = f"""
            SELECT
                COUNT_IF({column} IS NULL)   AS null_count,
                COUNT(DISTINCT {column})      AS distinct_count,
                MIN({column}::text)           AS min_value,
                MAX({column}::text)           AS max_value
            FROM {table}
        """
        df = self.execute(sql)
        row = df.row(0)  # pyright: ignore[reportUnknownMemberType]
        return ColumnStats(
            column_name=column,
            table_name=table,
            data_type="unknown",
            null_count=int(row[0]),
            distinct_count=int(row[1]),
            min_value=str(row[2]) if row[2] is not None else None,
            max_value=str(row[3]) if row[3] is not None else None,
        )
