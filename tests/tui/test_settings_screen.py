"""SettingsScreen: toggle profile settings, persist via ProfileManager.update."""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Input, Static, Switch

from labrat.profile.manager import ProfileManager
from labrat.profile.model import Profile
from labrat.screens.settings import SettingsScreen


class _Host(App[None]):
    def __init__(self, screen: SettingsScreen) -> None:
        super().__init__()
        self._screen = screen
        self.result: Profile | None = None

    def compose(self) -> ComposeResult:
        yield Static("")

    def on_mount(self) -> None:
        def _cb(result: Profile | None) -> None:
            self.result = result

        self.push_screen(self._screen, _cb)


async def test_save_persists_toggles(tmp_path: Path) -> None:
    mgr = ProfileManager(profiles_path=tmp_path / "profiles.json")
    profile = Profile(name="p1", dialect="duckdb")
    mgr.add(profile)
    host = _Host(SettingsScreen(profile, manager=mgr))
    async with host.run_test() as pilot:
        await pilot.pause()
        pilot.app.screen.query_one("#harvest-switch", Switch).value = True
        pilot.app.screen.query_one("#verify-switch", Switch).value = True
        await pilot.click("#save-btn")
        await pilot.pause()
    assert host.result is not None and host.result.harvest_opt_in is True
    assert mgr.get("p1").harvest_opt_in is True
    assert mgr.get("p1").verify_enabled is True


async def test_dbt_path_round_trips(tmp_path: Path) -> None:
    mgr = ProfileManager(profiles_path=tmp_path / "profiles.json")
    mgr.add(Profile(name="p1", dialect="duckdb"))
    host = _Host(SettingsScreen(mgr.get("p1"), manager=mgr))
    async with host.run_test() as pilot:
        await pilot.pause()
        pilot.app.screen.query_one("#dbt-path-input", Input).value = "/repo/dbt"
        await pilot.click("#save-btn")
        await pilot.pause()
    assert mgr.get("p1").dbt_project_path == "/repo/dbt"


async def test_cancel_dismisses_none(tmp_path: Path) -> None:
    mgr = ProfileManager(profiles_path=tmp_path / "profiles.json")
    profile = Profile(name="p1", dialect="duckdb")
    mgr.add(profile)
    host = _Host(SettingsScreen(profile, manager=mgr))
    async with host.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert host.result is None
    assert mgr.get("p1").harvest_opt_in is False
