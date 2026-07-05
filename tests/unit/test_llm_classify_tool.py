"""LlmClassifyTool: label-constrained per-row classification (LLM stubbed)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.llm_classify import LlmClassifyTool
from labrat.agent.tools.serialization import LedgerPayloadProvider
from labrat.db.duckdb_engine import DuckDBConnection

_LABELS = ["Business", "Sports", "Tech"]


def _make_conn(tmp_path: Path) -> DuckDBConnection:
    path = str(tmp_path / "classify.duckdb")
    raw = duckdb.connect(path)
    raw.execute("CREATE TABLE articles (id INTEGER, headline VARCHAR)")
    raw.execute(
        "INSERT INTO articles VALUES "
        "(1, 'Stocks rally on earnings'), "
        "(2, 'Local team wins the cup'), "
        "(3, 'New chip breaks records')"
    )
    raw.close()
    conn = DuckDBConnection(path=path, read_only=False)
    conn.connect()
    return conn


async def _fake_llm(prompt: str) -> str:
    if "Stocks" in prompt:
        return "Business"
    if "team" in prompt:
        return "Sports"
    return "Tech"


async def test_classify_tool_materializes_queryable_table(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_llm)
    tool = LlmClassifyTool()
    out = await tool.execute(
        ctx,
        tool.input_model(
            table="articles", text_column="headline", labels=_LABELS, key_columns=["id"]
        ),
    )
    assert out.ok
    assert out.result_table == "llm_classify_result"
    assert out.rows_processed == 3
    assert out.rows_failed == 0
    assert out.columns == ["id", "category"]
    df = conn.execute("SELECT category FROM llm_classify_result ORDER BY id")
    assert df["category"].to_list() == ["Business", "Sports", "Tech"]
    conn.disconnect()


async def test_classify_tool_out_of_label_is_failed_row(tmp_path: Path) -> None:
    async def rogue(prompt: str) -> str:
        return "Politics" if "team" in prompt else "Business"

    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=rogue)
    tool = LlmClassifyTool()
    out = await tool.execute(
        ctx,
        tool.input_model(
            table="articles", text_column="headline", labels=_LABELS, key_columns=["id"]
        ),
    )
    assert out.ok
    assert out.rows_failed == 1
    df = conn.execute("SELECT category FROM llm_classify_result WHERE id = 2")
    assert df["category"].to_list() == [None]
    conn.disconnect()


async def test_classify_tool_ledger_payload(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_llm)
    tool = LlmClassifyTool()
    out = await tool.execute(
        ctx, tool.input_model(table="articles", text_column="headline", labels=_LABELS)
    )
    assert isinstance(out, LedgerPayloadProvider)
    payload = out.ledger_payload()
    assert payload is not None
    kind, obj = payload
    assert kind == "table"
    assert isinstance(obj, pl.DataFrame)
    assert obj.columns == ["category"]
    conn.disconnect()


async def test_classify_tool_errors_without_llm_fn(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None)
    tool = LlmClassifyTool()
    out = await tool.execute(
        ctx, tool.input_model(table="articles", text_column="headline", labels=_LABELS)
    )
    assert not out.ok
    assert out.error is not None
    assert "LLM-enabled context" in out.error
    assert out.ledger_payload() is None
    conn.disconnect()


async def test_classify_tool_empty_labels_structured_error(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_llm)
    tool = LlmClassifyTool()
    out = await tool.execute(
        ctx, tool.input_model(table="articles", text_column="headline", labels=[])
    )
    assert not out.ok
    assert out.error is not None
    assert "labels" in out.error
    conn.disconnect()
