"""End-to-end: ingest → retrieve → footer shows (semantic_layer·fresh) / ·stale."""

from pathlib import Path

import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.search_reference_docs import SearchReferenceDocsTool
from labrat.db.catalog import Catalog, Column, Schema, Table
from labrat.maze.semantic_ingest import ingest_dbt_semantics
from labrat.maze.store import MazeStore
from labrat.widgets.turn_provenance import TurnProvenance

_FIXTURE = Path("tests/fixtures/sample_dbt_project/manifest_semantic.json")


def _catalog(cols: list[str]) -> Catalog:
    return Catalog(
        database_name="db",
        schemas=[
            Schema(
                name="main",
                tables=[
                    Table(
                        name="orders",
                        schema_name="main",
                        columns=[Column(name=c, data_type="INTEGER", nullable=True) for c in cols],
                    )
                ],
            )
        ],
    )


@pytest.fixture
def ingested(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Catalog:
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    cat = _catalog(["id", "total_amount"])
    store = MazeStore(project_root=tmp_path, home=tmp_path / "home", profile="default")
    ingest_dbt_semantics(
        manifest_path=_FIXTURE,
        catalog=cat,
        store=store,
        project_scent_dir=tmp_path / "labrat_maze" / "scent",
    )
    return cat


async def _footer(ctx: ToolContext) -> str:
    tool = SearchReferenceDocsTool()
    args = tool.input_model.model_validate({"question": "orders revenue measure"})
    out = await tool.execute(ctx, args)
    prov = TurnProvenance()
    prov.record_tool("search_reference_docs", True, out.model_dump_json())
    return prov.footer() or ""


async def test_fresh_semantic_footer(ingested: Catalog) -> None:
    ctx = ToolContext(connections={"main": object()}, catalogs={"main": ingested}, primary="main")
    footer = await _footer(ctx)
    assert "scent: orders (semantic_layer·fresh)" in footer


async def test_schema_drift_renders_stale(ingested: Catalog) -> None:
    drifted = _catalog(["id", "total_amount", "new_col"])  # schema changed
    ctx = ToolContext(connections={"main": object()}, catalogs={"main": drifted}, primary="main")
    footer = await _footer(ctx)
    assert "scent: orders (semantic_layer·stale)" in footer
