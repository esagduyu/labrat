"""End-to-end generate_scent + write_docs + benchmark-safety (#26b)."""

from __future__ import annotations

from pathlib import Path

import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.search_reference_docs import SearchReferenceDocsTool
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.cartographer import generate_scent, write_docs


def _conns(db: Path) -> tuple[dict[str, object], dict[str, object]]:
    conn = DuckDBConnection(db, read_only=True)
    conn.connect()
    return {"shop": conn}, {"shop": conn.introspect_catalog()}


async def test_generate_writes_retrievable_verified_doc(
    ecommerce_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connections, catalogs = _conns(ecommerce_db)
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


async def test_with_semantics_false_makes_zero_llm_calls(ecommerce_db: Path) -> None:
    connections, catalogs = _conns(ecommerce_db)
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


async def test_with_semantics_appends_draft_sections(ecommerce_db: Path) -> None:
    connections, catalogs = _conns(ecommerce_db)

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


async def test_code_name_section_present_on_deterministic_path(tmp_path) -> None:
    import duckdb

    from labrat.db.duckdb_engine import DuckDBConnection

    p = str(tmp_path / "clinical.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE clinical_info(icd_o_3_histology VARCHAR, histological_type VARCHAR)")
    raw.execute(
        "INSERT INTO clinical_info VALUES "
        "('9400/3','Astrocytoma'),('9401/3','Astrocytoma'),"
        "('9450/3','Oligodendroglioma'),('9382/3','Oligoastrocytoma')"
    )
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    try:
        docs = await generate_scent(
            connections={"clin": conn},
            catalogs={"clin": conn.introspect_catalog()},
            primary="clin",
            with_semantics=False,
        )
    finally:
        conn.disconnect()
    headings = {s.heading for s in docs[0].sections}
    assert "Code Columns" in headings
    body = next(s.body for s in docs[0].sections if s.heading == "Code Columns")
    assert "icd_o_3_histology" in body


async def test_no_code_name_section_when_no_pair(tmp_path) -> None:
    # byte-identity w.r.t. C2: a DuckDB dataset with no code/name pair gets no new section
    import duckdb

    from labrat.db.duckdb_engine import DuckDBConnection

    p = str(tmp_path / "plain.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE city(id INTEGER, name VARCHAR)")
    raw.execute("INSERT INTO city VALUES (1,'London'),(2,'Paris'),(3,'Berlin')")
    raw.close()
    conn = DuckDBConnection(p, read_only=True)
    conn.connect()
    try:
        docs = await generate_scent(
            connections={"c": conn},
            catalogs={"c": conn.introspect_catalog()},
            primary="c",
            with_semantics=False,
        )
    finally:
        conn.disconnect()
    assert "Code Columns" not in {s.heading for s in docs[0].sections}
