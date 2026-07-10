"""Pin-time capture + f8 share-answer produce a versioned artifact (Cheese v1 Task 5)."""

from pathlib import Path

import polars as pl
from textual.app import App, ComposeResult
from textual.widgets import Static

import labrat.cheese.store as cheese_store_mod
from labrat.screens.main import MainScreen
from labrat.widgets.chat_panel import ChatPanel
from labrat.widgets.results_table import ResultsTable


class _MainHost(App[None]):
    """Minimal app that pushes MainScreen for testing (mirrors test_main_screen.py)."""

    def compose(self) -> ComposeResult:
        yield Static("")

    def on_mount(self) -> None:
        self.push_screen(MainScreen())


def _redirect_cheese_roots(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cheese_store_mod, "DEFAULT_DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(cheese_store_mod, "DEFAULT_CHEESE_ROOT", tmp_path / "cheese")
    # _capture_finding's FindingsManager() uses its module default (~/.local/share/labrat)
    # — redirect it too so these tests never touch the real user's findings store.
    import labrat.thread.findings as findings_mod

    monkeypatch.setattr(findings_mod, "_DEFAULT_DIR", tmp_path / "findings_store")


async def test_capture_finding_writes_data_and_provenance(tmp_path: Path, monkeypatch) -> None:
    _redirect_cheese_roots(tmp_path, monkeypatch)
    async with _MainHost().run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, MainScreen)
        screen._last_sql = "SELECT 1 AS x"
        screen._last_user_prompt = "how many?"
        screen.query_one("#results-content", ResultsTable).load(pl.DataFrame({"x": [1, 2]}))
        finding = screen._capture_finding(question="how many?", sql="SELECT 1 AS x")
        assert finding is not None
        assert finding.results_ref == f"cheese://{finding.id}"
        assert (tmp_path / "data" / f"{finding.id}.parquet").exists()
        await pilot.pause()


async def test_capture_finding_skips_data_when_results_empty(tmp_path: Path, monkeypatch) -> None:
    """No rows and no chart → no data snapshot, but the Finding is still pinned."""
    _redirect_cheese_roots(tmp_path, monkeypatch)
    async with _MainHost().run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, MainScreen)
        screen._last_sql = "SELECT 1 AS x WHERE FALSE"
        finding = screen._capture_finding(question="q", sql=screen._last_sql)
        assert finding is not None
        assert finding.results_ref is None
        assert not (tmp_path / "data").exists()


async def test_capture_finding_returns_none_while_agent_busy(tmp_path: Path, monkeypatch) -> None:
    """A pin/share attempted mid-turn is refused, not snapshotted partially."""
    _redirect_cheese_roots(tmp_path, monkeypatch)
    async with _MainHost().run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, MainScreen)
        screen._last_sql = "SELECT 1 AS x"
        screen.query_one("#chat-content", ChatPanel).is_agent_busy = True
        finding = screen._capture_finding(question="q", sql=screen._last_sql)
        assert finding is None
        assert not (tmp_path / "data").exists()


async def test_f8_share_answer_exports_v1(tmp_path: Path, monkeypatch) -> None:
    _redirect_cheese_roots(tmp_path, monkeypatch)
    async with _MainHost().run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, MainScreen)
        screen._last_sql = "SELECT 1 AS x"
        screen._last_user_prompt = "how many?"
        screen.query_one("#results-content", ResultsTable).load(pl.DataFrame({"x": [1]}))
        await pilot.press("f8")
        await pilot.pause()
        cheeses = list((tmp_path / "cheese").iterdir())
        assert len(cheeses) == 1
        assert (cheeses[0] / "v1.html").exists()


async def test_f8_without_a_query_notifies_and_does_not_export(tmp_path: Path, monkeypatch) -> None:
    _redirect_cheese_roots(tmp_path, monkeypatch)
    async with _MainHost().run_test() as pilot:
        await pilot.pause()
        await pilot.press("f8")
        await pilot.pause()
        assert not (tmp_path / "cheese").exists()


async def test_f8_while_agent_busy_notifies_and_does_not_export(
    tmp_path: Path, monkeypatch
) -> None:
    _redirect_cheese_roots(tmp_path, monkeypatch)
    async with _MainHost().run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, MainScreen)
        screen._last_sql = "SELECT 1 AS x"
        screen.query_one("#results-content", ResultsTable).load(pl.DataFrame({"x": [1]}))
        screen.query_one("#chat-content", ChatPanel).is_agent_busy = True
        await pilot.press("f8")
        await pilot.pause()
        assert not (tmp_path / "cheese").exists()


async def test_new_user_turn_clears_last_chart(tmp_path: Path, monkeypatch) -> None:
    """_last_chart is stale-chart-pairing-hazard-prone; a new user turn clears it."""
    from labrat.chart.spec import ChartSpec, ChartType

    _redirect_cheese_roots(tmp_path, monkeypatch)
    async with _MainHost().run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, MainScreen)
        screen._last_chart = (
            ChartSpec(chart_type=ChartType.bar, x="a", y="b"),
            pl.DataFrame({"a": [1], "b": [2]}),
        )
        screen.post_message(ChatPanel.UserMessage("a new question"))
        await pilot.pause()
        assert screen._last_chart is None
        assert screen._last_user_prompt == "a new question"
