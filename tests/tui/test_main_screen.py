"""Tests for the main three-pane layout screen."""

from collections.abc import Callable

from textual.app import App, ComposeResult
from textual.widgets import Static

from labrat.screens.main import MainScreen


class _MainHost(App[None]):
    """Minimal app that pushes MainScreen for testing."""

    def compose(self) -> ComposeResult:
        yield Static("")

    def on_mount(self) -> None:
        self.push_screen(MainScreen())


async def test_main_screen_mounts() -> None:
    """All four panes are present after mount."""
    async with _MainHost().run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert screen.query_one("#chat-pane")
        assert screen.query_one("#editor-pane")
        assert screen.query_one("#results-pane")
        assert screen.query_one("#schema-pane")


async def test_ctrl_h_toggles_schema() -> None:
    """Ctrl+H hides the schema pane; second press shows it again."""
    async with _MainHost().run_test() as pilot:
        await pilot.pause()
        schema = pilot.app.screen.query_one("#schema-pane")
        assert schema.display
        await pilot.press("ctrl+h")
        await pilot.pause()
        assert not schema.display
        await pilot.press("ctrl+h")
        await pilot.pause()
        assert schema.display


async def test_ctrl_l_toggles_chat() -> None:
    """Ctrl+L hides the chat pane; second press shows it again."""
    async with _MainHost().run_test() as pilot:
        await pilot.pause()
        chat = pilot.app.screen.query_one("#chat-pane")
        assert chat.display
        await pilot.press("ctrl+l")
        await pilot.pause()
        assert not chat.display
        await pilot.press("ctrl+l")
        await pilot.pause()
        assert chat.display


async def test_narrow_mode_on_resize() -> None:
    """Resizing below 80 columns adds 'narrow' class; resizing back removes it."""
    async with _MainHost().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert "narrow" not in screen.classes
        await pilot.resize_terminal(79, 24)
        assert "narrow" in screen.classes
        await pilot.resize_terminal(100, 30)
        assert "narrow" not in screen.classes


def test_main_screen_snapshot_medium(snap_compare: Callable[..., bool]) -> None:
    """Snapshot of three-pane layout at 120x40."""
    assert snap_compare(_MainHost(), terminal_size=(120, 40))


def test_main_screen_snapshot_large(snap_compare: Callable[..., bool]) -> None:
    """Snapshot of three-pane layout at 200x60."""
    assert snap_compare(_MainHost(), terminal_size=(200, 60))


def test_main_screen_snapshot_small(snap_compare: Callable[..., bool]) -> None:
    """Snapshot of layout at 80x24 (minimum supported size)."""
    assert snap_compare(_MainHost(), terminal_size=(80, 24))
