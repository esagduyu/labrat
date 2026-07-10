"""MainScreen._wrap_subagent_runner — protects the M3 draft-capture baseline
(_last_draft_sql / _last_sql) around a sub-agent dispatch (session-followups R3).

The connected on_mount block wraps ctx.subagent_runner with this immediately
after build_agent_session returns; these tests exercise the wrapper directly
per the task brief (the restore-after-run / propagate-on-raise contract),
plus a connected-mode check that the wiring is actually installed."""

from __future__ import annotations

from pathlib import Path

import pytest
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


def _screen(ecommerce_db: Path) -> MainScreen:
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    conn.connect()
    return MainScreen(
        profile="dispatchprof",
        dialect="duckdb",
        catalog=conn.introspect_catalog(),
        connection=conn,
        profile_obj=Profile(name="dispatchprof", dialect="duckdb", path=str(ecommerce_db)),
    )


async def test_connected_mount_wraps_ctx_subagent_runner(ecommerce_db: Path, monkeypatch) -> None:
    """After build_agent_session returns, ctx.subagent_runner must be the
    MainScreen wrapper (not the raw session.py closure) — proves the wiring
    line in the connected on_mount block actually ran."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    screen = _screen(ecommerce_db)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        loop = pilot.app.screen.query_one("#chat-content", ChatPanel)._agent_loop
        ctx = loop._ctx
        assert ctx.subagent_runner is not None
        assert ctx.subagent_runner.__name__ == "wrapped"  # session.py's is "_run_subagent"


async def test_wrap_subagent_runner_restores_baseline_after_run(
    ecommerce_db: Path, monkeypatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    screen = _screen(ecommerce_db)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        screen._last_draft_sql = "PARENT DRAFT"
        screen._last_sql = "PARENT SQL"

        async def fake_inner(**kw):
            # simulate a sub-agent draft firing the parent's UI callback mid-run
            screen._last_draft_sql = "SUB DRAFT"
            screen._last_sql = "SUB SQL"
            return ("sub", 1, 1)

        wrapped = screen._wrap_subagent_runner(fake_inner)
        result = await wrapped(seed_prompt="s", artifact_refs=[], max_turns=1, max_tool_calls=1)

        assert result == ("sub", 1, 1)
        assert screen._last_draft_sql == "PARENT DRAFT"  # restored
        assert screen._last_sql == "PARENT SQL"


async def test_wrap_subagent_runner_restores_baseline_when_inner_raises(
    ecommerce_db: Path, monkeypatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    screen = _screen(ecommerce_db)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        screen._last_draft_sql = "PARENT DRAFT"
        screen._last_sql = "PARENT SQL"

        async def fake_inner(**kw):
            screen._last_draft_sql = "SUB DRAFT"
            screen._last_sql = "SUB SQL"
            raise RuntimeError("boom")

        wrapped = screen._wrap_subagent_runner(fake_inner)

        with pytest.raises(RuntimeError, match="boom"):
            await wrapped(seed_prompt="s", artifact_refs=[], max_turns=1, max_tool_calls=1)

        assert screen._last_draft_sql == "PARENT DRAFT"  # restored despite raise
        assert screen._last_sql == "PARENT SQL"
