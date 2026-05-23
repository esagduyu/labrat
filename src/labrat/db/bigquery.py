"""BigQuery connection adapter (M25).

Uses google-cloud-bigquery. Install: uv add "google-cloud-bigquery"
Application Default Credentials (ADC) are used for authentication.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from labrat.db.base import Connection
from labrat.db.catalog import Catalog, Column, ColumnStats, Schema, Table


class BigQueryConnection(Connection):
    """BigQuery connection using google-cloud-bigquery."""

    def __init__(
        self,
        project: str,
        dataset: str | None = None,
        credentials: Any = None,
    ) -> None:
        self._project = project
        self._dataset = dataset
        self._credentials = credentials
        self._client: Any = None

    def __repr__(self) -> str:
        status = "connected" if self._client is not None else "disconnected"
        return f"BigQueryConnection(project={self._project!r}, {status})"

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def connect(self) -> None:
        from google.cloud import bigquery  # type: ignore[import-untyped]

        self._client = bigquery.Client(  # pyright: ignore[reportUnknownMemberType]
            project=self._project,
            credentials=self._credentials,
        )

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.close()  # pyright: ignore[reportUnknownMemberType]
            self._client = None

    # ── query execution ───────────────────────────────────────────────────────

    def execute(self, sql: str) -> pl.DataFrame:
        if self._client is None:
            raise RuntimeError("Not connected.")
        job = self._client.query(sql)  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
        result = job.result()  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
        rows = [dict(row) for row in result]  # pyright: ignore[reportUnknownVariableType]
        if not rows:
            return pl.DataFrame()
        return pl.from_dicts(rows)  # pyright: ignore[reportUnknownArgumentType]

    def explain(self, sql: str) -> str:
        # BigQuery doesn't have EXPLAIN; return query plan from job statistics
        if self._client is None:
            raise RuntimeError("Not connected.")
        job = self._client.query(sql)  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
        job.result()  # pyright: ignore[reportUnknownMemberType]
        stats = job.query_plan  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
        return str(stats)

    def sample_table(self, table: str, n: int = 10) -> pl.DataFrame:
        return self.execute(f"SELECT * FROM {table} LIMIT {n}")

    # ── catalog introspection ─────────────────────────────────────────────────

    def introspect_catalog(self) -> Catalog:
        if self._client is None:
            raise RuntimeError("Not connected.")
        sql = f"""
            SELECT table_schema, table_name, column_name, data_type, is_nullable
            FROM `{self._project}`.INFORMATION_SCHEMA.COLUMNS
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
        return Catalog(database_name=self._project, schemas=schemas)

    # ── column statistics ─────────────────────────────────────────────────────

    def column_stats(self, table: str, column: str) -> ColumnStats:
        sql = f"""
            SELECT
                COUNTIF({column} IS NULL)     AS null_count,
                COUNT(DISTINCT {column})       AS distinct_count,
                CAST(MIN({column}) AS STRING)  AS min_value,
                CAST(MAX({column}) AS STRING)  AS max_value
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
