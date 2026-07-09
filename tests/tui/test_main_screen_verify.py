"""Profile.verify_enabled must reach the loop as an attached LLMVerifier."""

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


def _screen(ecommerce_db: Path, *, verify: bool) -> MainScreen:
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    conn.connect()
    return MainScreen(
        profile="vprof",
        dialect="duckdb",
        catalog=conn.introspect_catalog(),
        connection=conn,
        profile_obj=Profile(
            name="vprof", dialect="duckdb", path=str(ecommerce_db), verify_enabled=verify
        ),
    )


async def test_verify_enabled_attaches_verifier(ecommerce_db: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    async with _Host(_screen(ecommerce_db, verify=True)).run_test() as pilot:
        await pilot.pause()
        loop = pilot.app.screen.query_one("#chat-content", ChatPanel)._agent_loop
        assert loop._verifier is not None
        # Guard: it must be the sufficiency LLMVerifier, never consensus.
        from labrat.agent.verifier import LLMVerifier

        assert isinstance(loop._verifier, LLMVerifier)


async def test_verify_disabled_attaches_none(ecommerce_db: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    async with _Host(_screen(ecommerce_db, verify=False)).run_test() as pilot:
        await pilot.pause()
        loop = pilot.app.screen.query_one("#chat-content", ChatPanel)._agent_loop
        assert loop._verifier is None
