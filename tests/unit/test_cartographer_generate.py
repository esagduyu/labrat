"""End-to-end generate_scent + write_docs + benchmark-safety (#26b)."""

from __future__ import annotations

from pathlib import Path

import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.search_reference_docs import SearchReferenceDocsTool
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import generate_scent, write_docs

_FIXTURE = "tests/fixtures/sample_dbs/ecommerce.duckdb"


def _conns() -> tuple[dict[str, object], dict[str, object]]:
    conn = DuckDBConnection(Path(_FIXTURE), read_only=True)
    conn.connect()
    return {"shop": conn}, {"shop": conn.introspect_catalog()}


async def test_generate_writes_retrievable_verified_doc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connections, catalogs = _conns()
    try:
        docs = await generate_scent(connections=connections, catalogs=catalogs, primary="shop")
    finally:
        connections["shop"].disconnect()  # type: ignore[attr-defined]

    assert len(docs) == 1
    doc = docs[0]
    assert doc.domain == "shop"
    assert doc.confidence == "draft"
    headings = {s.heading for s in doc.sections}
    assert {"Quick Reference", "Key Tables", "Dimensions"} <= headings
    assert all(s.source == "verified" for s in doc.sections)  # no LLM → all verified

    # write into a store and confirm #26a can retrieve it
    out = tmp_path / "labrat_maze" / "scent"
    write_docs(docs, out)
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "empty_home"))
    tool = SearchReferenceDocsTool()
    res = await tool.execute(
        ToolContext(profile_name="default"),
        tool.input_model(question="how do I join orders to customers?"),
    )
    assert any(r.domain == "shop" for r in res.results)


async def test_with_semantics_false_makes_zero_llm_calls() -> None:
    connections, catalogs = _conns()
    calls = {"n": 0}

    async def _spy(prompt: str) -> str:
        calls["n"] += 1
        return "## Gotchas\n- x"

    try:
        await generate_scent(
            connections=connections,
            catalogs=catalogs,
            primary="shop",
            with_semantics=False,
            llm_fn=_spy,
        )
    finally:
        connections["shop"].disconnect()  # type: ignore[attr-defined]
    assert calls["n"] == 0  # benchmark-safety: deterministic-only path never calls the model


async def test_with_semantics_appends_draft_sections() -> None:
    connections, catalogs = _conns()

    async def _llm(prompt: str) -> str:
        return "## Gotchas\n- Exclude is_test rows from metrics."

    try:
        docs = await generate_scent(
            connections=connections,
            catalogs=catalogs,
            primary="shop",
            with_semantics=True,
            llm_fn=_llm,
        )
    finally:
        connections["shop"].disconnect()  # type: ignore[attr-defined]
    doc = docs[0]
    gotchas = [s for s in doc.sections if s.heading == "Gotchas"]
    assert len(gotchas) == 1
    assert gotchas[0].source == "draft"
