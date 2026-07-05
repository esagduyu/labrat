"""sample_rows ledger_payload hook: the sampled DataFrame is declared for the ledger."""

from __future__ import annotations

import polars as pl

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.sample_rows import SampleRowsTool


class _StubConn:
    def sample_table(self, table: str, n: int = 10) -> pl.DataFrame:
        return pl.DataFrame({"a": list(range(n)), "b": ["v"] * n})


async def test_sample_rows_exposes_table_payload() -> None:
    tool = SampleRowsTool()
    out = await tool.execute(
        ToolContext(connection=_StubConn(), catalog=None, primary="main"),
        tool.input_model(table="t", n=4),
    )
    payload = out.ledger_payload()
    assert payload is not None
    kind, obj = payload
    assert kind == "table"
    assert isinstance(obj, pl.DataFrame)
    assert obj.height == 4
    assert obj.columns == ["a", "b"]
    # existing serialised surface unchanged
    assert out.rows == [[str(i), "v"] for i in range(4)]
