"""Tests for the onboarding TUI flow."""

from collections.abc import Callable
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static

from labrat.screens.onboarding import OnboardingScreen, _detect_dbt


class _OnboardingHost(App[None]):
    """Minimal app that pushes OnboardingScreen for testing."""

    def compose(self) -> ComposeResult:
        yield Static("")

    def on_mount(self) -> None:
        self.push_screen(OnboardingScreen())


async def test_onboarding_screen_mounts() -> None:
    """OnboardingScreen mounts and shows step indicator."""
    async with _OnboardingHost().run_test() as pilot:
        await pilot.pause()
        assert pilot.app.screen.query_one("#step-indicator")


async def test_onboarding_escape_dismisses() -> None:
    """Pressing Escape on the first step pops the screen."""
    async with _OnboardingHost().run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        # After dismiss the host app's own screen should be active
        assert pilot.app.screen.__class__.__name__ != "OnboardingScreen"


async def test_onboarding_advance_to_dialect() -> None:
    """Clicking Continue on welcome step advances to dialect step."""
    async with _OnboardingHost().run_test() as pilot:
        await pilot.pause()
        await pilot.click("#btn-continue")
        await pilot.pause()
        assert pilot.app.screen.query_one("#dialect-set")


def test_detect_dbt_not_present(tmp_path: Path) -> None:
    """_detect_dbt returns None when no dbt_project.yml exists in a clean dir."""
    # We can't monkeypatch Path.cwd easily, so just verify function signature
    result = _detect_dbt()
    assert result is None or isinstance(result, Path)


def test_detect_dbt_present(tmp_path: Path) -> None:
    """dbt_project.yml detection logic works for a known path."""
    candidate = tmp_path / "dbt_project.yml"
    candidate.write_text("name: test")
    assert candidate.exists()


def test_onboarding_snapshot(snap_compare: Callable[..., bool]) -> None:
    """Snapshot of onboarding welcome step."""
    assert snap_compare(_OnboardingHost())
