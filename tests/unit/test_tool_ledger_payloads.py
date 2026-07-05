"""profile_dataset + column_stats ledger_payload hooks (json kind)."""

from __future__ import annotations

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.column_stats import ColumnStatsTool
from labrat.agent.tools.profile_dataset import ProfileDatasetTool
from labrat.db.catalog import Catalog, Column, ColumnStats, Schema, Table

_CATALOG = Catalog(
    database_name="db",
    schemas=[
        Schema(
            name="main",
            tables=[
                Table(
                    name="t",
                    schema_name="main",
                    columns=[Column(name="a", data_type="INTEGER", nullable=True)],
                    foreign_keys=[],
                    row_count=7,
                )
            ],
        )
    ],
)


class _StubStatsConn:
    def column_stats(self, table: str, column: str) -> ColumnStats:
        return ColumnStats(
            column_name=column,
            table_name=table,
            data_type="INTEGER",
            null_count=0,
            distinct_count=7,
            min_value="1",
            max_value="7",
        )


async def test_profile_dataset_exposes_json_payload() -> None:
    ctx = ToolContext(connection=object(), catalog=_CATALOG, primary="main")
    tool = ProfileDatasetTool()
    # sample_rows=0 + row_count set on the catalog → the connection is never touched
    out = await tool.execute(ctx, tool.input_model(sample_rows=0))
    payload = out.ledger_payload()
    assert payload is not None
    kind, obj = payload
    assert kind == "json"
    assert isinstance(obj, dict)
    assert obj["database"] == "main"
    assert obj["tables"][0]["name"] == "t"
    assert obj["tables"][0]["row_count"] == 7


async def test_column_stats_exposes_json_payload() -> None:
    ctx = ToolContext(connection=_StubStatsConn(), catalog=None, primary="main")
    tool = ColumnStatsTool()
    out = await tool.execute(ctx, tool.input_model(table="t", column="a"))
    payload = out.ledger_payload()
    assert payload is not None
    kind, obj = payload
    assert kind == "json"
    assert isinstance(obj, dict)
    assert obj["column_name"] == "a"
    assert obj["distinct_count"] == 7
