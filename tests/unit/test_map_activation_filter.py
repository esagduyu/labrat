"""Map activation filters retrieval; empty/None is byte-identical (benchmark guarantee)."""

from __future__ import annotations

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.search_reference_docs import SearchReferenceDocsTool
from labrat.agent.tools.search_trails import SearchTrailsTool
from labrat.maze.document import ScentDoc, Section
from labrat.maze.map import build_map_doc
from labrat.maze.store import MazeStore


def _seed(root):
    store = MazeStore(project_root=root, home=root / "h", profile="default")
    for d in ("subscriptions", "campaigns"):
        store.write_doc(
            ScentDoc(
                domain=d,
                kind="scent",
                sections=[Section(heading="Gotchas", body=f"{d} revenue churn note")],
            ),
            kind="scent",
        )
    store.write_doc(
        build_map_doc("revenue", scent=["subscriptions"], trails=[], prompts=[]), kind="map"
    )


def _seed_trails(root):
    store = MazeStore(project_root=root, home=root / "h", profile="default")
    for d in ("retention", "attribution"):
        store.write_doc(
            ScentDoc(
                domain=d,
                kind="trail",
                sections=[Section(heading="Steps", body=f"{d} revenue churn steps")],
            ),
            kind="trail",
        )
    store.write_doc(
        build_map_doc("revenue", scent=[], trails=["retention"], prompts=[]), kind="map"
    )


async def test_no_active_maps_is_unchanged(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    tool = SearchReferenceDocsTool()
    ctx = ToolContext(connections={}, catalogs={}, primary="main")  # active_maps None
    out = await tool.execute(ctx, tool.input_model(question="revenue churn"))
    domains = {r.domain for r in out.results}
    assert domains == {"subscriptions", "campaigns"}  # both, as today


async def test_active_map_filters_to_members(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    tool = SearchReferenceDocsTool()
    ctx = ToolContext(connections={}, catalogs={}, primary="main", active_maps=["revenue"])
    out = await tool.execute(ctx, tool.input_model(question="revenue churn"))
    domains = {r.domain for r in out.results}
    assert domains == {"subscriptions"}  # campaigns filtered out — not a revenue-Map member


async def test_empty_list_is_unchanged(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    tool = SearchReferenceDocsTool()
    ctx = ToolContext(connections={}, catalogs={}, primary="main", active_maps=[])
    out = await tool.execute(ctx, tool.input_model(question="revenue churn"))
    assert {r.domain for r in out.results} == {"subscriptions", "campaigns"}  # empty == no filter


async def test_trails_no_active_maps_is_unchanged(tmp_path, monkeypatch):
    _seed_trails(tmp_path)
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    tool = SearchTrailsTool()
    ctx = ToolContext(connections={}, catalogs={}, primary="main")  # active_maps None
    out = await tool.execute(ctx, tool.input_model(intent="revenue churn"))
    slugs = {r.intent_slug for r in out.results}
    assert slugs == {"retention", "attribution"}  # both, as today


async def test_trails_active_map_filters_to_members(tmp_path, monkeypatch):
    _seed_trails(tmp_path)
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    tool = SearchTrailsTool()
    ctx = ToolContext(connections={}, catalogs={}, primary="main", active_maps=["revenue"])
    out = await tool.execute(ctx, tool.input_model(intent="revenue churn"))
    slugs = {r.intent_slug for r in out.results}
    assert slugs == {"retention"}  # attribution filtered out — not a revenue-Map member


async def test_trails_empty_list_is_unchanged(tmp_path, monkeypatch):
    _seed_trails(tmp_path)
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    tool = SearchTrailsTool()
    ctx = ToolContext(connections={}, catalogs={}, primary="main", active_maps=[])
    out = await tool.execute(ctx, tool.input_model(intent="revenue churn"))
    assert {r.intent_slug for r in out.results} == {"retention", "attribution"}
