"""Map v1: activation (additive retrieval filter), auto-seed, author/curate (Task 4)."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Input, Label, Static, TextArea

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.search_reference_docs import SearchReferenceDocsTool
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.document import ScentDoc, Section
from labrat.maze.map import build_map_doc, scent_members, trail_members
from labrat.maze.store import MazeStore
from labrat.profile.model import Profile
from labrat.screens.main import MainScreen
from labrat.screens.maps import MapActivateScreen, MapEditScreen, _overview_body


class _Host(App[None]):
    """Minimal app hosting a single screen (mirrors test_trail_review.py)."""

    def __init__(self, screen) -> None:
        super().__init__()
        self._screen = screen

    def compose(self) -> ComposeResult:
        yield Static("")

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def _seed_scent(store: MazeStore, *domains: str) -> None:
    for d in domains:
        store.write_doc(
            ScentDoc(
                domain=d, kind="scent", sections=[Section(heading="Gotchas", body=f"{d} note")]
            ),
            kind="scent",
        )


def _seed_trail(store: MazeStore, *domains: str) -> None:
    for d in domains:
        store.write_doc(
            ScentDoc(
                domain=d, kind="trail", sections=[Section(heading="Steps", body=f"{d} steps")]
            ),
            kind="trail",
        )


# ── (a)+(b): activation mutates in place, scoping + restoring retrieval ─────


async def test_map_activate_toggle_scopes_and_restores_retrieval(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    store = MazeStore(project_root=tmp_path, home=tmp_path / "home", profile="default")
    _seed_scent(store, "subscriptions", "campaigns")
    store.write_doc(
        build_map_doc("revenue", scent=["subscriptions"], trails=[], prompts=[]), kind="map"
    )

    active_maps: list[str] = []
    screen = MapActivateScreen(store, active_maps)
    tool = SearchReferenceDocsTool()
    ctx = ToolContext(connections={}, catalogs={}, primary="main", active_maps=active_maps)

    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        table = pilot.app.screen.query_one("#maps-table", DataTable)
        assert table.row_count == 1

        # Not yet activated: search is unscoped (byte-identical to no-Map).
        out = await tool.execute(ctx, tool.input_model(question="subscriptions campaigns"))
        assert {r.domain for r in out.results} == {"subscriptions", "campaigns"}

        # (a) Activating mutates the SAME list object in place.
        await pilot.press("space")
        await pilot.pause()
        assert active_maps == ["revenue"]
        assert ctx.active_maps is active_maps  # identity: the live link the tools read

        out = await tool.execute(ctx, tool.input_model(question="subscriptions campaigns"))
        assert {r.domain for r in out.results} == {"subscriptions"}  # scoped

        # (b) Deactivating restores full grounding.
        await pilot.press("space")
        await pilot.pause()
        assert active_maps == []

        out = await tool.execute(ctx, tool.input_model(question="subscriptions campaigns"))
        assert {r.domain for r in out.results} == {"subscriptions", "campaigns"}  # restored


async def test_map_activate_screen_lists_only_existing_maps(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    store = MazeStore(project_root=tmp_path, home=tmp_path / "home", profile="default")
    _seed_scent(store, "subscriptions")  # no Map doc written

    active_maps: list[str] = []
    screen = MapActivateScreen(store, active_maps)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        table = pilot.app.screen.query_one("#maps-table", DataTable)
        assert table.row_count == 0

        # No rows -> toggling can't activate a phantom slug.
        await pilot.press("space")
        await pilot.pause()
        assert active_maps == []


async def test_map_activate_toggle_keeps_cursor_on_toggled_map(tmp_path: Path, monkeypatch) -> None:
    """M2 regression: toggling a non-first row must leave the cursor on that
    same map (not reset to row 0), so a following space acts on the map the
    user is looking at, not whatever landed on top after the reload."""
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    store = MazeStore(project_root=tmp_path, home=tmp_path / "home", profile="default")
    _seed_scent(store, "campaigns", "revenue", "subscriptions")
    for name in ("campaigns", "revenue", "subscriptions"):
        store.write_doc(build_map_doc(name, scent=[name], trails=[], prompts=[]), kind="map")

    active_maps: list[str] = []
    screen = MapActivateScreen(store, active_maps)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        table = pilot.app.screen.query_one("#maps-table", DataTable)
        assert table.row_count == 3

        # Rows sort alphabetically: campaigns(0), revenue(1), subscriptions(2).
        # Move the cursor onto "revenue" (row 1) and toggle it.
        table.cursor_coordinate = Coordinate(1, 0)
        await pilot.pause()
        await pilot.press("space")
        await pilot.pause()

        assert active_maps == ["revenue"]
        # Cursor must still be on the "revenue" row, not reset to row 0.
        key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key
        assert str(key.value) == "revenue"

        # A second toggle from here must act on "revenue" again (deactivate it),
        # not on whatever ended up at row 0.
        await pilot.press("space")
        await pilot.pause()
        assert active_maps == []


# ── identity wiring: the SAME list object reaches ToolContext via MainScreen ─


def _main_screen(ecommerce_db: Path, tmp_path: Path) -> MainScreen:
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    conn.connect()
    return MainScreen(
        profile="mapprof",
        dialect="duckdb",
        catalog=conn.introspect_catalog(),
        connection=conn,
        profile_obj=Profile(name="mapprof", dialect="duckdb", path=str(ecommerce_db)),
        scent_dir=tmp_path / "scent",
    )


async def test_active_maps_same_object_wired_into_toolcontext(
    ecommerce_db: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    screen = _main_screen(ecommerce_db, tmp_path)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()

        assert screen._agent_loop is not None
        ctx = screen._agent_loop._ctx
        assert ctx.active_maps is screen._active_maps  # same object, not a copy

        screen._active_maps.append("revenue")  # mutate in place, as MapActivateScreen does
        assert ctx.active_maps == ["revenue"]  # live link sees it without reassignment


# ── (c): Cartographer dbt auto-seed writes map/ skeletons ───────────────────


def _dbt_manifest(tmp_path: Path) -> Path:
    nodes = {
        "model.acme.mrr": {
            "resource_type": "model",
            "name": "mrr",
            "alias": "mrr",
            "fqn": ["acme", "marts", "finance", "mrr"],
        },
        "model.acme.invoices": {
            "resource_type": "model",
            "name": "invoices",
            "alias": "invoices",
            "fqn": ["acme", "marts", "finance", "invoices"],
        },
        "model.acme.events": {
            "resource_type": "model",
            "name": "events",
            "alias": "events",
            "fqn": ["acme", "marts", "product", "events"],
        },
    }
    import json

    p = tmp_path / "manifest.json"
    p.write_text(json.dumps({"nodes": nodes}))
    return p


def _dbt_screen(tmp_path: Path, *, dbt: bool = True) -> MainScreen:
    return MainScreen(
        profile="dbtprof",
        dialect="duckdb",
        connection=None,  # no live DB needed — auto-seed only touches the Maze store
        profile_obj=Profile(
            name="dbtprof", dialect="duckdb", dbt_project_path="/configured" if dbt else None
        ),
        dbt_manifest_override=_dbt_manifest(tmp_path) if dbt else None,
    )


async def test_auto_seed_maps_writes_skeletons(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    store = MazeStore(project_root=tmp_path, home=tmp_path / "home", profile="dbtprof")
    _seed_scent(store, "mrr", "invoices", "events")

    screen = _dbt_screen(tmp_path)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        n = screen.action_auto_seed_maps()
        assert n == 2

    map_dir = tmp_path / "labrat_maze" / "map"
    assert (map_dir / "finance.md").exists()
    assert (map_dir / "product.md").exists()
    reloaded = {d.domain: d for d in store.docs(kind="map")}
    assert set(scent_members(reloaded["finance"])) == {"mrr", "invoices"}
    assert scent_members(reloaded["product"]) == ["events"]


async def test_auto_seed_maps_skips_already_seeded_domains(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    store = MazeStore(project_root=tmp_path, home=tmp_path / "home", profile="dbtprof")
    _seed_scent(store, "mrr", "invoices", "events")

    screen = _dbt_screen(tmp_path)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        first = screen.action_auto_seed_maps()
        second = screen.action_auto_seed_maps()
    assert first == 2
    assert second == 0  # already seeded — no duplicate writes


async def test_auto_seed_maps_no_dbt_project_configured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    screen = _dbt_screen(tmp_path, dbt=False)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        n = screen.action_auto_seed_maps()
    assert n == 0
    assert not (tmp_path / "labrat_maze" / "map").exists()


# ── author/curate: MapEditScreen ─────────────────────────────────────────────


async def test_map_edit_screen_creates_new_map_with_members(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    store = MazeStore(project_root=tmp_path, home=tmp_path / "home", profile="default")
    _seed_scent(store, "subscriptions", "campaigns")
    _seed_trail(store, "compute-mrr")

    screen = MapEditScreen(store, None)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        table = pilot.app.screen.query_one("#members-table", DataTable)
        assert table.row_count == 3  # 2 scent + 1 trail

        pilot.app.screen.query_one("#slug-input", Input).value = "Revenue!"
        pilot.app.screen.query_one("#overview", TextArea).text = "Revenue domain bundle."
        pilot.app.screen.query_one("#prompts", TextArea).text = "What's our MRR?\nWhat's churn?"

        # "space" toggles a row only while the DataTable has focus — otherwise
        # a focused Input/TextArea consumes it as a literal keystroke.
        table.focus()
        await pilot.pause()

        # Toggle in the "subscriptions" scent row and the "compute-mrr" trail row.
        for row_index in range(table.row_count):
            key = table.coordinate_to_cell_key(Coordinate(row_index, 0)).row_key
            if str(key.value) in {"scent:subscriptions", "trail:compute-mrr"}:
                table.cursor_coordinate = Coordinate(row_index, 0)
                await pilot.press("space")
                await pilot.pause()

        await pilot.press("ctrl+s")
        await pilot.pause()

    map_path = tmp_path / "labrat_maze" / "map" / "revenue.md"
    assert map_path.is_file()  # "Revenue!" slugifies to "revenue"
    doc = store.load_domain("revenue", kind="map", scope="project")
    assert doc is not None
    assert scent_members(doc) == ["subscriptions"]
    assert trail_members(doc) == ["compute-mrr"]


async def test_map_edit_screen_edits_existing_map(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    store = MazeStore(project_root=tmp_path, home=tmp_path / "home", profile="default")
    _seed_scent(store, "subscriptions", "campaigns")
    existing = build_map_doc(
        "revenue", scent=["subscriptions"], trails=[], prompts=["What's our ARR?"], overview="old"
    )
    store.write_doc(existing, kind="map")

    screen = MapEditScreen(store, existing)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        # Existing Map: no slug input, curating in place.
        assert not pilot.app.screen.query(Input)
        overview = pilot.app.screen.query_one("#overview", TextArea)
        assert overview.text == "old"
        overview.text = "Revenue: subscriptions + campaigns."

        table = pilot.app.screen.query_one("#members-table", DataTable)
        table.focus()
        await pilot.pause()
        for row_index in range(table.row_count):
            key = table.coordinate_to_cell_key(Coordinate(row_index, 0)).row_key
            if str(key.value) == "scent:campaigns":
                table.cursor_coordinate = Coordinate(row_index, 0)
                await pilot.press("space")  # add campaigns to the bundle
                await pilot.pause()

        await pilot.press("ctrl+s")
        await pilot.pause()

    doc = store.load_domain("revenue", kind="map", scope="project")
    assert doc is not None
    assert set(scent_members(doc)) == {"subscriptions", "campaigns"}


async def test_map_edit_screen_new_map_blocks_slug_collision(tmp_path: Path, monkeypatch) -> None:
    """M1 regression: saving a New Map whose slug collides with an existing
    curated Map must NOT clobber it — block the save and leave the existing
    Map's content untouched."""
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    store = MazeStore(project_root=tmp_path, home=tmp_path / "home", profile="default")
    _seed_scent(store, "subscriptions", "campaigns")
    existing = build_map_doc(
        "revenue",
        scent=["subscriptions"],
        trails=[],
        prompts=["What's our ARR?"],
        overview="Curated revenue overview — do not lose me.",
    )
    store.write_doc(existing, kind="map")
    map_path = tmp_path / "labrat_maze" / "map" / "revenue.md"
    written_before = map_path.read_text(encoding="utf-8")

    screen = MapEditScreen(store, None)  # New Map path
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        # "Revenue" slugifies to "revenue" — collides with the existing curated Map.
        pilot.app.screen.query_one("#slug-input", Input).value = "Revenue"
        pilot.app.screen.query_one("#overview", TextArea).text = "Clobbering overview."

        await pilot.press("ctrl+s")
        await pilot.pause()

        # Blocked: screen stays open, status warns about the collision.
        assert isinstance(pilot.app.screen, MapEditScreen)
        status = pilot.app.screen.query_one("#status", Label)
        status_text = str(status.render())
        assert "already exists" in status_text
        assert "revenue" in status_text

    # No write happened — the curated Map's content is byte-identical.
    assert map_path.read_text(encoding="utf-8") == written_before
    doc = store.load_domain("revenue", kind="map", scope="project")
    assert doc is not None
    assert scent_members(doc) == ["subscriptions"]
    assert _overview_body(doc) == "Curated revenue overview — do not lose me."


async def test_map_edit_screen_blocks_contaminated_overview(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    store = MazeStore(project_root=tmp_path, home=tmp_path / "home", profile="default")

    screen = MapEditScreen(store, None)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        pilot.app.screen.query_one("#slug-input", Input).value = "revenue"
        pilot.app.screen.query_one("#overview", TextArea).text = "See ground_truth.csv for answers."

        await pilot.press("ctrl+s")
        await pilot.pause()

        # Blocked: screen stays open, status shows the audit verdict.
        assert isinstance(pilot.app.screen, MapEditScreen)
        status = pilot.app.screen.query_one("#status", Label)
        assert "contamination audit" in str(status.render())

    assert not (tmp_path / "labrat_maze" / "map").exists()
