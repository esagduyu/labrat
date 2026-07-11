"""Map v1.1: status-bar active-Maps indicator + first-connect nudge (UI only)."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static

from labrat.db.duckdb_engine import DuckDBConnection
from labrat.maze.map import build_map_doc
from labrat.maze.store import MazeStore
from labrat.profile.model import Profile
from labrat.screens.main import MainScreen, _StatusBar


class _Host(App[None]):
    """Minimal app hosting a single screen (mirrors test_main_screen_scent.py)."""

    def __init__(self, screen: MainScreen) -> None:
        super().__init__()
        self._screen = screen

    def compose(self) -> ComposeResult:
        yield Static("")

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def _store(tmp_path: Path, profile: str) -> MazeStore:
    return MazeStore(project_root=tmp_path, home=tmp_path / "home", profile=profile)


def _screen(
    ecommerce_db: Path,
    tmp_path: Path,
    *,
    profile_name: str = "mapprof",
    dbt: bool = False,
) -> MainScreen:
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    conn.connect()
    return MainScreen(
        profile=profile_name,
        dialect="duckdb",
        catalog=conn.introspect_catalog(),
        connection=conn,
        profile_obj=Profile(
            name=profile_name,
            dialect="duckdb",
            path=str(ecommerce_db),
            dbt_project_path="/configured" if dbt else None,
        ),
        scent_dir=tmp_path / "scent",
        project_root_override=tmp_path,
    )


async def test_status_bar_shows_active_maps_after_modal_closes(
    ecommerce_db: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    profile_name = "mapprof"
    store = _store(tmp_path, profile_name)
    store.write_doc(
        build_map_doc("revenue", scent=[], trails=[], prompts=[]), kind="map", scope="project"
    )

    async with _Host(
        _screen(ecommerce_db, tmp_path, profile_name=profile_name)
    ).run_test() as pilot:
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
        screen = pilot.app.screen
        assert isinstance(screen, MainScreen)

        # No maps active yet — status bar unchanged.
        bars = list(screen.query(_StatusBar))
        assert bars
        assert "\U0001f5fa Maps:" not in bars[0].render()

        # Activate "revenue" via the real modal flow (space toggles, escape dismisses
        # and fires the refresh callback).
        await pilot.press("ctrl+shift+p")
        await pilot.pause()
        await pilot.press("space")
        await pilot.press("escape")
        await pilot.pause()

        bars = list(screen.query(_StatusBar))
        assert any("\U0001f5fa Maps: revenue" in bar.render() for bar in bars)

        # Deactivate + refresh → segment gone again.
        await pilot.press("ctrl+shift+p")
        await pilot.pause()
        await pilot.press("space")
        await pilot.press("escape")
        await pilot.pause()

        bars = list(screen.query(_StatusBar))
        assert all("\U0001f5fa Maps:" not in bar.render() for bar in bars)


async def test_status_bar_empty_when_no_active_maps(
    ecommerce_db: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    async with _Host(_screen(ecommerce_db, tmp_path)).run_test() as pilot:
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
        screen = pilot.app.screen
        assert isinstance(screen, MainScreen)
        assert screen._active_maps == []
        for bar in screen.query(_StatusBar):
            assert "\U0001f5fa Maps:" not in bar.render()


async def test_first_connect_nudges_when_dbt_and_no_maps(
    ecommerce_db: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    messages: list[str] = []
    screen = _screen(ecommerce_db, tmp_path, profile_name="dbtprof", dbt=True)
    monkeypatch.setattr(
        screen, "notify", lambda msg, *a, **k: messages.append(str(msg)), raising=False
    )

    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
        assert any("Ctrl+Shift+P" in m and "Auto-seed" in m for m in messages)


async def test_first_connect_no_nudge_when_maps_present(
    ecommerce_db: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    profile_name = "dbtprof2"
    store = _store(tmp_path, profile_name)
    store.write_doc(
        build_map_doc("revenue", scent=[], trails=[], prompts=[]), kind="map", scope="project"
    )

    messages: list[str] = []
    screen = _screen(ecommerce_db, tmp_path, profile_name=profile_name, dbt=True)
    monkeypatch.setattr(
        screen, "notify", lambda msg, *a, **k: messages.append(str(msg)), raising=False
    )

    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
        assert not any("Ctrl+Shift+P" in m and "Auto-seed" in m for m in messages)
