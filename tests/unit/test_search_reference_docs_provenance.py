"""Additive provenance/freshness fields on search_reference_docs output (spec 3.3)."""

from pathlib import Path

import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.search_reference_docs import SearchReferenceDocsTool
from labrat.db.catalog import Catalog, Column, Schema, Table
from labrat.maze.document import ScentDoc, Section, render_document
from labrat.maze.staleness import fingerprint_from_catalog


def _catalog() -> Catalog:
    return Catalog(
        database_name="db",
        schemas=[
            Schema(
                name="main",
                tables=[
                    Table(
                        name="orders",
                        schema_name="main",
                        columns=[
                            Column(name="id", data_type="INTEGER", nullable=False),
                        ],
                    )
                ],
            )
        ],
    )


def _write_doc(maze_dir: Path, *, schema_hash: str | None, source: str = "verified") -> None:
    doc = ScentDoc(
        domain="orders",
        sections=[
            Section(
                heading="Key Tables",
                body="- orders join key id",
                source=source,
                schema_hash=schema_hash,
            )
        ],
    )
    scent = maze_dir / "scent"
    scent.mkdir(parents=True, exist_ok=True)
    (scent / "orders.md").write_text(render_document(doc), encoding="utf-8")


@pytest.fixture
def env_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    maze_dir = tmp_path / "labrat_maze"
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    return maze_dir


async def _run(ctx: ToolContext):
    tool = SearchReferenceDocsTool()
    args = tool.input_model.model_validate({"question": "orders join key"})
    return await tool.execute(ctx, args)


async def test_fresh_section_labelled(env_store: Path) -> None:
    cat = _catalog()
    _write_doc(env_store, schema_hash=fingerprint_from_catalog(cat))
    ctx = ToolContext(connections={"main": object()}, catalogs={"main": cat}, primary="main")
    out = await _run(ctx)
    sec = out.results[0].sections[0]
    assert sec.source == "verified" and sec.fresh is True
    assert out.results[0].best_source == "verified"
    assert out.results[0].stale is False


async def test_hash_mismatch_is_stale(env_store: Path) -> None:
    _write_doc(env_store, schema_hash="deadbeef")
    ctx = ToolContext(connections={"main": object()}, catalogs={"main": _catalog()}, primary="main")
    out = await _run(ctx)
    assert out.results[0].sections[0].fresh is False
    assert out.results[0].stale is True


async def test_missing_meta_is_unknown_not_fresh(env_store: Path) -> None:
    _write_doc(env_store, schema_hash=None)
    ctx = ToolContext(connections={"main": object()}, catalogs={"main": _catalog()}, primary="main")
    out = await _run(ctx)
    assert out.results[0].sections[0].fresh is None
    assert out.results[0].stale is None


async def test_no_catalog_degrades_to_unknown(env_store: Path) -> None:
    _write_doc(env_store, schema_hash="anything")
    out = await _run(ToolContext())  # default ctx: no catalogs
    assert out.results[0].sections[0].fresh is None
    assert out.results[0].stale is None
    assert out.results[0].best_source == "verified"  # tier still reported
