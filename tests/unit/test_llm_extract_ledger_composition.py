"""An over-budget llm_extract result is bounded in history, retrievable in full."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import polars as pl

from labrat.agent.tools.base import DispatchResult, ToolContext
from labrat.agent.tools.llm_extract import LlmExtractTool
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.results.store import ResultStore
from labrat.runtime.context_ledger import ContextLedger, LedgerBudget


async def _fake_llm(prompt: str) -> str:
    return json.dumps({"topic": "energy"})


async def test_over_budget_extract_is_bounded_and_retrievable(tmp_path: Path) -> None:
    path = str(tmp_path / "ledger.duckdb")
    raw = duckdb.connect(path)
    raw.execute("CREATE TABLE docs (id INTEGER, body VARCHAR)")
    raw.executemany("INSERT INTO docs VALUES (?, ?)", [(i, f"doc number {i}") for i in range(20)])
    raw.close()
    conn = DuckDBConnection(path=path, read_only=False)
    conn.connect()

    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_llm)
    tool = LlmExtractTool()
    out = await tool.execute(
        ctx,
        tool.input_model(
            table="docs",
            text_column="body",
            json_schema={"properties": {"topic": {"type": "string"}}},
            key_columns=["id"],
        ),
    )
    assert out.ok
    assert out.rows_processed == 20

    # A tight budget forces truncation: the model sees a bounded summary + ref...
    ledger = ContextLedger(
        ResultStore(tmp_path / "artifacts"), budget=LedgerBudget(max_rows=5, max_bytes=200)
    )
    visible = ledger.record("llm_extract", DispatchResult(ok=True, value=out))
    assert visible.truncated
    assert visible.full_row_count == 20
    assert visible.artifact_ref is not None

    # ...while the FULL extraction is retrievable from the store...
    stored = ledger.store.get(visible.artifact_ref)
    assert isinstance(stored, pl.DataFrame)
    assert stored.height == 20
    assert stored.columns == ["id", "topic"]

    # ...AND queryable/joinable in DuckDB by table name.
    joined = conn.execute(
        "SELECT COUNT(*) AS n FROM docs d JOIN llm_extract_result r ON d.id = r.id"
    )
    assert joined["n"].to_list() == [20]
    conn.disconnect()
