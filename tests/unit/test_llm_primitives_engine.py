"""extract_rows engine: SELECT + per-row llm_fn fan-out + assembly (LLM stubbed)."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import polars as pl
import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.llm_primitives import ExtractResult, extract_rows
from labrat.db.duckdb_engine import DuckDBConnection

_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"inventor": {"type": "string"}, "year": {"type": "string"}},
}


def _make_conn(tmp_path: Path) -> DuckDBConnection:
    path = str(tmp_path / "engine.duckdb")
    raw = duckdb.connect(path)
    raw.execute("CREATE TABLE patents (id INTEGER, abstract VARCHAR)")
    raw.execute(
        "INSERT INTO patents VALUES "
        "(1, 'Invented by Ada in 1843'), "
        "(2, 'Invented by Grace in 1952'), "
        "(3, 'Invented by Alan in 1936')"
    )
    raw.close()
    conn = DuckDBConnection(path=path, read_only=False)
    conn.connect()
    return conn


async def _fake_extract_llm(prompt: str) -> str:
    if "Ada" in prompt:
        return json.dumps({"inventor": "Ada", "year": "1843"})
    if "Grace" in prompt:
        return json.dumps({"inventor": "Grace", "year": "1952"})
    return json.dumps({"inventor": "Alan", "year": "1936"})


async def test_extract_rows_assembles_dataframe(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_extract_llm)
    result = await extract_rows(
        ctx, table="patents", text_column="abstract", key_columns=["id"], spec=_SCHEMA
    )
    assert isinstance(result, ExtractResult)
    assert result.rows_processed == 3
    assert result.rows_failed == 0
    assert result.df.columns == ["id", "inventor", "year"]
    assert result.df.height == 3
    by_id = dict(zip(result.df["id"].to_list(), result.df["inventor"].to_list(), strict=True))
    assert by_id == {1: "Ada", 2: "Grace", 3: "Alan"}
    assert result.df["inventor"].dtype == pl.Utf8
    assert result.df["year"].dtype == pl.Utf8
    conn.disconnect()


async def test_extract_rows_requires_llm_fn(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None)
    with pytest.raises(RuntimeError, match="llm_fn"):
        await extract_rows(
            ctx, table="patents", text_column="abstract", key_columns=["id"], spec=_SCHEMA
        )
    conn.disconnect()


async def test_extract_rows_rejects_unsafe_identifier(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_extract_llm)
    with pytest.raises(ValueError, match="identifier"):
        await extract_rows(
            ctx,
            table="patents; DROP TABLE patents",
            text_column="abstract",
            key_columns=["id"],
            spec=_SCHEMA,
        )
    conn.disconnect()


async def test_extract_rows_rejects_empty_schema(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=_fake_extract_llm)
    with pytest.raises(ValueError, match="field"):
        await extract_rows(
            ctx, table="patents", text_column="abstract", key_columns=["id"], spec={}
        )
    conn.disconnect()


async def test_extract_rows_malformed_reply_yields_null_row(tmp_path: Path) -> None:
    async def flaky(prompt: str) -> str:
        if "Grace" in prompt:
            return "Sure! Grace invented it."  # not JSON → failed row
        return json.dumps({"inventor": "x", "year": "y"})

    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=flaky)
    result = await extract_rows(
        ctx, table="patents", text_column="abstract", key_columns=["id"], spec=_SCHEMA
    )
    assert result.rows_processed == 3
    assert result.rows_failed == 1
    row = result.df.filter(pl.col("id") == 2)
    assert row["inventor"].to_list() == [None]
    assert row["year"].to_list() == [None]
    conn.disconnect()


async def test_extract_rows_llm_exception_yields_null_row(tmp_path: Path) -> None:
    async def exploding(prompt: str) -> str:
        if "Alan" in prompt:
            raise TimeoutError("provider timeout")
        return json.dumps({"inventor": "x", "year": "y"})

    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=exploding)
    result = await extract_rows(
        ctx, table="patents", text_column="abstract", key_columns=["id"], spec=_SCHEMA
    )
    assert result.rows_processed == 3
    assert result.rows_failed == 1
    conn.disconnect()


async def test_extract_rows_max_rows_caps_select_and_calls(tmp_path: Path) -> None:
    calls: list[str] = []

    async def counting(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({"inventor": "x", "year": "y"})

    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=counting)
    result = await extract_rows(
        ctx,
        table="patents",
        text_column="abstract",
        key_columns=["id"],
        spec=_SCHEMA,
        max_rows=2,
    )
    assert result.rows_processed == 2
    assert len(calls) == 2
    conn.disconnect()


async def test_extract_rows_limit_clamped_to_max_rows(tmp_path: Path) -> None:
    calls: list[str] = []

    async def counting(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps({"inventor": "x", "year": "y"})

    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=counting)
    result = await extract_rows(
        ctx,
        table="patents",
        text_column="abstract",
        key_columns=["id"],
        spec=_SCHEMA,
        limit=50,
        max_rows=2,
    )
    assert result.rows_processed == 2
    assert len(calls) == 2
    conn.disconnect()


async def test_extract_rows_classify_mode(tmp_path: Path) -> None:
    async def classify_llm(prompt: str) -> str:
        return "Sports" if "Alan" in prompt else "Business"

    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=classify_llm)
    result = await extract_rows(
        ctx,
        table="patents",
        text_column="abstract",
        key_columns=["id"],
        spec=["Business", "Sports"],
    )
    assert result.df.columns == ["id", "category"]
    assert result.rows_failed == 0
    by_id = dict(zip(result.df["id"].to_list(), result.df["category"].to_list(), strict=True))
    assert by_id == {1: "Business", 2: "Business", 3: "Sports"}
    conn.disconnect()


async def test_extract_rows_classify_out_of_label_fails_row(tmp_path: Path) -> None:
    async def rogue_llm(prompt: str) -> str:
        return "Politics" if "Grace" in prompt else "Business"

    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=rogue_llm)
    result = await extract_rows(
        ctx,
        table="patents",
        text_column="abstract",
        key_columns=["id"],
        spec=["Business", "Sports"],
    )
    assert result.rows_failed == 1
    assert result.df.filter(pl.col("id") == 2)["category"].to_list() == [None]
    conn.disconnect()


async def test_extract_rows_classify_empty_labels_rejected(tmp_path: Path) -> None:
    async def never(prompt: str) -> str:
        raise AssertionError("must not be called")

    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=never)
    with pytest.raises(ValueError, match="labels"):
        await extract_rows(
            ctx, table="patents", text_column="abstract", key_columns=["id"], spec=[]
        )
    conn.disconnect()


async def test_extract_rows_where_filters(tmp_path: Path) -> None:
    async def classify_llm(prompt: str) -> str:
        return "Business"

    conn = _make_conn(tmp_path)
    ctx = ToolContext(connection=conn, catalog=None, llm_fn=classify_llm)
    result = await extract_rows(
        ctx,
        table="patents",
        text_column="abstract",
        key_columns=["id"],
        spec=["Business", "Sports"],
        where="id > 1",
    )
    assert result.rows_processed == 2
    assert set(result.df["id"].to_list()) == {2, 3}
    conn.disconnect()


async def test_extract_rows_null_text_fails_row_without_llm_call(tmp_path: Path) -> None:
    path = str(tmp_path / "nulls.duckdb")
    raw = duckdb.connect(path)
    raw.execute("CREATE TABLE notes (id INTEGER, body VARCHAR)")
    raw.execute("INSERT INTO notes VALUES (1, 'hello'), (2, NULL)")
    raw.close()
    conn = DuckDBConnection(path=path, read_only=False)
    conn.connect()

    calls: list[str] = []

    async def counting(prompt: str) -> str:
        calls.append(prompt)
        return "Business"

    ctx = ToolContext(connection=conn, catalog=None, llm_fn=counting)
    result = await extract_rows(
        ctx, table="notes", text_column="body", key_columns=["id"], spec=["Business", "Sports"]
    )
    assert result.rows_processed == 2
    assert result.rows_failed == 1
    assert len(calls) == 1
    conn.disconnect()
