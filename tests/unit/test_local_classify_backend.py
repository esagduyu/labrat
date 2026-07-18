"""local-embed classification backend (Lever Pack v2 T2): zero-LLM-token classify.

The backend classifies rows by cosine similarity between row text and label
prototypes using the `semantic` extra's embedder. Tests inject a stub embedder —
no model downloads, no LLM calls.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.llm_classify import LlmClassifyTool
from labrat.db.duckdb_engine import DuckDBConnection


class _StubEmbedder:
    """Deterministic embedder: 'sport…' texts near [1,0], 'money…' near [0,1]."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            lowered = text.lower()
            if "sport" in lowered or "game" in lowered:
                out.append([1.0, 0.05])
            elif "money" in lowered or "market" in lowered:
                out.append([0.05, 1.0])
            else:
                out.append([0.5, 0.5])
        return out


@pytest.fixture()
def ctx(tmp_path: Path) -> Iterator[ToolContext]:
    import duckdb

    db = tmp_path / "articles.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE articles(id INTEGER, body VARCHAR)")
    con.execute(
        "INSERT INTO articles VALUES "
        "(1, 'the game went to overtime, a sports classic'), "
        "(2, 'markets rallied as money poured in'), "
        "(3, 'sports fans cheered the game')"
    )
    con.close()
    conn = DuckDBConnection(db, read_only=False)
    conn.connect()
    yield ToolContext(
        connection=conn,
        catalog=conn.introspect_catalog(),
        llm_classify_backend="local-embed",
    )
    conn.disconnect()


def _args(tool: LlmClassifyTool, **overrides: object) -> object:
    payload: dict[str, object] = {
        "table": "articles",
        "text_column": "body",
        "key_columns": ["id"],
        "labels": ["sports", "finance"],
    }
    payload.update(overrides)
    return tool.input_model(**payload)


async def test_local_embed_classifies_rows_into_result_table(ctx: ToolContext) -> None:
    import labrat.agent.tools.local_classify as local_mod

    tool = LlmClassifyTool()
    ctx.llm_classify_row_budget = 200

    orig = local_mod.get_default_embedder
    local_mod.get_default_embedder = lambda: _StubEmbedder()  # type: ignore[assignment]
    try:
        out = await tool.execute(ctx, _args(tool))  # type: ignore[arg-type]
    finally:
        local_mod.get_default_embedder = orig  # type: ignore[assignment]

    assert out.ok is True
    assert out.rows_processed == 3
    assert out.requests_made == 0
    assert ctx.llm_classify_rows_used == 3
    df = ctx.connection.execute("SELECT id, category FROM llm_classify_result ORDER BY id")
    cats = {int(r[0]): r[1] for r in df.iter_rows()}
    assert cats[1] == "sports" and cats[3] == "sports"
    assert cats[2] == "finance"


async def test_local_embed_without_embedder_self_errors(ctx: ToolContext) -> None:
    import labrat.agent.tools.local_classify as local_mod

    tool = LlmClassifyTool()
    orig = local_mod.get_default_embedder
    local_mod.get_default_embedder = lambda: None  # type: ignore[assignment]
    try:
        out = await tool.execute(ctx, _args(tool))  # type: ignore[arg-type]
    finally:
        local_mod.get_default_embedder = orig  # type: ignore[assignment]
    assert out.ok is False
    assert out.error is not None and "semantic" in out.error


async def test_local_embed_respects_cumulative_row_budget(ctx: ToolContext) -> None:
    import labrat.agent.tools.local_classify as local_mod

    tool = LlmClassifyTool()
    ctx.llm_classify_row_budget = 2
    orig = local_mod.get_default_embedder
    local_mod.get_default_embedder = lambda: _StubEmbedder()  # type: ignore[assignment]
    try:
        first = await tool.execute(ctx, _args(tool))  # type: ignore[arg-type]
        second = await tool.execute(ctx, _args(tool))  # type: ignore[arg-type]
    finally:
        local_mod.get_default_embedder = orig  # type: ignore[assignment]
    assert first.ok is True and first.rows_processed == 2
    assert ctx.llm_classify_rows_used == 2
    assert second.ok is False
    assert second.error is not None and "budget exhausted" in second.error


async def test_local_embed_needs_no_llm_fn(ctx: ToolContext) -> None:
    # ctx has no llm_fn / llm_classify_fn at all; local backend must not require one.
    assert ctx.llm_fn is None and ctx.llm_classify_fn is None
    import labrat.agent.tools.local_classify as local_mod

    tool = LlmClassifyTool()
    orig = local_mod.get_default_embedder
    local_mod.get_default_embedder = lambda: _StubEmbedder()  # type: ignore[assignment]
    try:
        out = await tool.execute(ctx, _args(tool))  # type: ignore[arg-type]
    finally:
        local_mod.get_default_embedder = orig  # type: ignore[assignment]
    assert out.ok is True


def test_tool_context_validates_backend() -> None:
    with pytest.raises(ValueError, match="llm_classify_backend"):
        ToolContext(connections={}, catalogs={}, primary="x", llm_classify_backend="bogus")
