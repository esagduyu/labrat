"""Capture seams + harvest gating on MainScreen."""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static

from labrat.db.duckdb_engine import DuckDBConnection
from labrat.profile.model import Profile
from labrat.screens.main import MainScreen
from labrat.widgets.chat_panel import ChatPanel


class _Host(App[None]):
    def __init__(self, screen: MainScreen) -> None:
        super().__init__()
        self._screen = screen

    def compose(self) -> ComposeResult:
        yield Static("")

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def _screen(ecommerce_db: Path, *, opt_in: bool) -> MainScreen:
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    conn.connect()
    return MainScreen(
        profile="hprof",
        dialect="duckdb",
        catalog=conn.introspect_catalog(),
        connection=conn,
        profile_obj=Profile(
            name="hprof", dialect="duckdb", path=str(ecommerce_db), harvest_opt_in=opt_in
        ),
    )


async def test_user_message_after_sql_lands_in_buffer(ecommerce_db: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    screen = _screen(ecommerce_db, opt_in=True)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        screen._last_sql = "SELECT count(*) FROM orders"  # simulate a prior agent answer
        panel = pilot.app.screen.query_one("#chat-content", ChatPanel)
        panel.post_message(ChatPanel.UserMessage("no — exclude test orders"))
        await pilot.pause()
        assert screen._correction_buffer.pending_count == 1


async def test_edit_divergence_lands_in_buffer(ecommerce_db: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    screen = _screen(ecommerce_db, opt_in=True)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        screen._last_draft_sql = "SELECT count(*) FROM orders"
        screen._record_edit_if_diverged("SELECT count(*) FROM orders WHERE status != 'x'")
        assert screen._correction_buffer.pending_count == 1
        assert screen._last_draft_sql is None  # recorded once, then cleared


async def test_harvest_action_without_opt_in_notifies(ecommerce_db: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    screen = _screen(ecommerce_db, opt_in=False)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        screen.action_harvest_review()
        await pilot.pause()
        # Gated off: no HarvestReviewScreen pushed.
        assert pilot.app.screen is screen
