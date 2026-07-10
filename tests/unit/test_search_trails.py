"""search_trails: intent-keyed retrieval over kind='trail' docs."""

from pathlib import Path

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.search_trails import SearchTrailsTool
from labrat.maze.document import ScentDoc, Section
from labrat.maze.store import MazeStore


def _seed_trail(root: Path) -> None:
    store = MazeStore(project_root=root, home=root / "home", profile="default")
    doc = ScentDoc(
        domain="compute-monthly-retention",
        kind="trail",
        tables=["events"],
        sections=[
            Section(
                heading="When to use",
                body="Computing monthly user retention or cohort return rates.",
                source="verified",
            ),
            Section(heading="Reference SQL", body="```sql\nSELECT ...\n```", source="verified"),
        ],
    )
    store.write_doc(doc, scope="project", kind="trail")


async def test_intent_match_returns_trail(tmp_path, monkeypatch):
    _seed_trail(tmp_path)
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    tool = SearchTrailsTool()
    out = await tool.execute(
        ToolContext(connections={}, catalogs={}, primary="main"),
        tool.input_model(intent="how to compute retention"),
    )
    assert len(out.results) == 1
    assert out.results[0].intent_slug == "compute-monthly-retention"
    assert out.results[0].when_to_use is not None


async def test_empty_store_returns_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    tool = SearchTrailsTool()
    out = await tool.execute(
        ToolContext(connections={}, catalogs={}, primary="main"),
        tool.input_model(intent="anything"),
    )
    assert out.results == []


def test_registered_in_data_tools():
    from labrat.agent.data_tools import build_data_tools_registry

    reg = build_data_tools_registry()
    assert "search_trails" in [t.name for t in reg.tools]
