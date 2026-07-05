"""LlmExtractTool: materialize + ledger_payload + llm_fn-gating (LLM stubbed)."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import polars as pl

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.llm_extract import LlmExtractTool
from labrat.agent.tools.serialization import LedgerPayloadProvider
from labrat.db.duckdb_engine import DuckDBConnection

_SCHEMA: dict[str, object] = {
    "properties": {"brand": {"type": "string"}, "product": {"type": "string"}}
}


def _make_conn(tmp_path: Path) -> DuckDBConnection:
    path = str(tmp_path / "tool.duckdb")
    raw = duckdb.connect(path)
    raw.execute("CREATE TABLE reviews (id INTEGER, body VARCHAR)")
    raw.execute(
        "INSERT INTO reviews VALUES (1, 'Great phone by Acme'), (2, 'Bad laptop by Zenith')"
    )
    raw.close()
    conn = DuckDBConnection(path=path, read_only=False)
    conn.connect()
    return conn


async def _fake_llm(prompt: str) -> str:
    if "Acme" in prompt:
        return json.dumps({"brand": "Acme", "product": "phone"})
    return json.dumps({"brand": "Zenith", "product": "laptop"})


async def test_extract_tool_materializes_queryable_table(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_llm)
    tool = LlmExtractTool()
    out = await tool.execute(
        ctx,
        tool.input_model(
            table="reviews", text_column="body", json_schema=_SCHEMA, key_columns=["id"]
        ),
    )
    assert out.ok
    assert out.result_table == "llm_extract_result"
    assert out.rows_processed == 2
    assert out.rows_failed == 0
    assert out.columns == ["id", "brand", "product"]
    # The result table is queryable/joinable by a follow-up SQL call.
    df = conn.execute("SELECT brand FROM llm_extract_result ORDER BY id")
    assert df["brand"].to_list() == ["Acme", "Zenith"]
    conn.disconnect()


async def test_extract_tool_ledger_payload(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_llm)
    tool = LlmExtractTool()
    out = await tool.execute(
        ctx,
        tool.input_model(
            table="reviews", text_column="body", json_schema=_SCHEMA, key_columns=["id"]
        ),
    )
    assert isinstance(out, LedgerPayloadProvider)
    payload = out.ledger_payload()
    assert payload is not None
    kind, obj = payload
    assert kind == "table"
    assert isinstance(obj, pl.DataFrame)
    assert obj.height == 2
    assert obj.columns == ["id", "brand", "product"]
    conn.disconnect()


async def test_extract_tool_errors_without_llm_fn(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None)  # llm_fn defaults None
    tool = LlmExtractTool()
    out = await tool.execute(
        ctx, tool.input_model(table="reviews", text_column="body", json_schema=_SCHEMA)
    )
    assert not out.ok
    assert out.error is not None
    assert "LLM-enabled context" in out.error
    assert out.ledger_payload() is None
    conn.disconnect()


async def test_extract_tool_custom_result_table(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_llm)
    tool = LlmExtractTool()
    out = await tool.execute(
        ctx,
        tool.input_model(
            table="reviews",
            text_column="body",
            json_schema=_SCHEMA,
            key_columns=["id"],
            result_table="brands",
        ),
    )
    assert out.ok
    assert out.result_table == "brands"
    assert conn.execute("SELECT COUNT(*) AS n FROM brands")["n"].to_list() == [2]
    conn.disconnect()


async def test_extract_tool_structured_error_on_bad_result_table(tmp_path: Path) -> None:
    calls: list[str] = []

    async def counting(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({"brand": "x", "product": "y"})

    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=counting)
    tool = LlmExtractTool()
    out = await tool.execute(
        ctx,
        tool.input_model(
            table="reviews",
            text_column="body",
            json_schema=_SCHEMA,
            result_table="bad name; drop",
        ),
    )
    assert not out.ok
    assert out.error is not None
    assert len(calls) == 0  # validated up-front: no per-row calls were burned
    conn.disconnect()


async def test_extract_tool_structured_error_on_engine_failure(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_llm)
    tool = LlmExtractTool()
    out = await tool.execute(
        ctx,
        tool.input_model(table="no_such_table", text_column="body", json_schema=_SCHEMA),
    )
    assert not out.ok
    assert out.error is not None
    conn.disconnect()


async def test_extract_tool_requires_duckdb_primary() -> None:
    ctx = ToolContext(connection=object(), catalog=None, llm_fn=_fake_llm)
    tool = LlmExtractTool()
    out = await tool.execute(
        ctx, tool.input_model(table="reviews", text_column="body", json_schema=_SCHEMA)
    )
    assert not out.ok
    assert out.error is not None
    assert "DuckDB" in out.error
