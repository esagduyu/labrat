"""Pin-time capture + f8 share-answer produce a versioned artifact (Cheese v1 Task 5)."""

from pathlib import Path

import polars as pl
from textual.app import App, ComposeResult
from textual.widgets import Static

import labrat.cheese.store as cheese_store_mod
from labrat.chart.spec import ChartSpec, ChartType
from labrat.db.duckdb_engine import DuckDBConnection
from labrat.profile.model import Profile
from labrat.screens.main import MainScreen
from labrat.widgets.chat_panel import ChatPanel
from labrat.widgets.query_editor import QueryEditor
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


class _ConnectedHost(App[None]):
    """Host that pushes an already-connected MainScreen (mirrors
    test_main_screen_agent_wiring.py's `_Host`; needed here because
    `_execute_sql` requires a live `self._connection`)."""

    def __init__(self, screen: MainScreen) -> None:
        super().__init__()
        self._screen = screen

    def compose(self) -> ComposeResult:
        yield Static("")

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def _connected_screen(ecommerce_db: Path) -> MainScreen:
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    profile = Profile(name="testprof", dialect="duckdb", path=str(ecommerce_db))
    return MainScreen(
        profile="testprof",
        dialect="duckdb",
        catalog=catalog,
        connection=conn,
        profile_obj=profile,
    )


async def test_execute_sql_clears_last_chart(
    tmp_path: Path, monkeypatch, ecommerce_db: Path
) -> None:
    """A manually-run query invalidates any chart left over from an agent turn
    — otherwise pinning right after would pair fresh rows with a stale chart
    (t5-M1)."""
    _redirect_cheese_roots(tmp_path, monkeypatch)
    screen = _connected_screen(ecommerce_db)
    async with _ConnectedHost(screen).run_test() as pilot:
        await pilot.pause()
        screen._last_chart = (
            ChartSpec(chart_type=ChartType.bar, x="a", y="b"),
            pl.DataFrame({"a": [1], "b": [2]}),
        )
        screen.on_query_editor_run_requested(QueryEditor.RunRequested("SELECT 1 AS x"))
        await pilot.app.workers.wait_for_complete()
        assert screen._last_chart is None


async def test_capture_finding_with_chart_writes_chart_and_spec(
    tmp_path: Path, monkeypatch
) -> None:
    """A real chart at capture time renders a PNG and stamps chart_spec (t5-M2)."""
    _redirect_cheese_roots(tmp_path, monkeypatch)
    async with _MainHost().run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, MainScreen)
        screen._last_sql = "SELECT 1 AS x"
        screen.query_one("#results-content", ResultsTable).load(
            pl.DataFrame({"a": ["x"], "b": [2]})
        )
        screen._last_chart = (
            ChartSpec(chart_type=ChartType.bar, x="a", y="b"),
            pl.DataFrame({"a": ["x"], "b": [2]}),
        )
        finding = screen._capture_finding(question="chart it", sql="SELECT 1 AS x")
        assert finding is not None
        assert finding.chart_spec is not None
        assert (tmp_path / "data" / f"{finding.id}.chart.png").exists()


async def test_capture_finding_chart_render_failure_omits_chart(
    tmp_path: Path, monkeypatch
) -> None:
    """render_image raising never blocks the pin — the chart is just omitted."""
    _redirect_cheese_roots(tmp_path, monkeypatch)

    def _boom(spec: object, df: object) -> bytes:
        raise RuntimeError("boom")

    monkeypatch.setattr("labrat.chart.render_image.render_image", _boom)
    async with _MainHost().run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, MainScreen)
        screen._last_sql = "SELECT 1 AS x"
        screen.query_one("#results-content", ResultsTable).load(
            pl.DataFrame({"a": ["x"], "b": [2]})
        )
        screen._last_chart = (
            ChartSpec(chart_type=ChartType.bar, x="a", y="b"),
            pl.DataFrame({"a": ["x"], "b": [2]}),
        )
        finding = screen._capture_finding(question="q", sql="SELECT 1 AS x")
        assert finding is not None
        assert (tmp_path / "data" / f"{finding.id}.parquet").exists()
        assert not (tmp_path / "data" / f"{finding.id}.chart.png").exists()


async def test_execute_sql_clears_stale_turn_provenance(
    tmp_path: Path, monkeypatch, ecommerce_db: Path
) -> None:
    """A manually-run query must not inherit a prior agent turn's grounding
    trust block — otherwise pin/f8 stamps the OLD turn's provenance onto SQL
    the agent never produced (whole-branch F1)."""
    from labrat.widgets.turn_provenance import TurnProvenance

    _redirect_cheese_roots(tmp_path, monkeypatch)
    screen = _connected_screen(ecommerce_db)
    async with _ConnectedHost(screen).run_test() as pilot:
        await pilot.pause()
        chat = screen.query_one("#chat-content", ChatPanel)
        tp = TurnProvenance()
        tp.record_tool("run_sql", True, "")
        chat.last_turn_provenance = tp
        screen.on_query_editor_run_requested(QueryEditor.RunRequested("SELECT 1 AS x"))
        await pilot.app.workers.wait_for_complete()
        assert chat.last_turn_provenance is None
        # A subsequent pin over this hand-run SQL must be unattested, not
        # inherit the stale agent-turn provenance.
        screen._last_sql = "SELECT 1 AS x"
        finding = screen._capture_finding(question="q", sql="SELECT 1 AS x")
        assert finding is not None
        assert finding.provenance is None


async def test_thread_switch_clears_stale_turn_provenance(tmp_path: Path, monkeypatch) -> None:
    """Switching threads must drop the old thread's grounding provenance —
    it belongs to a different thread's turn (whole-branch F1)."""
    from labrat.thread.manager import ThreadManager
    from labrat.widgets.turn_provenance import TurnProvenance

    _redirect_cheese_roots(tmp_path, monkeypatch)
    async with _MainHost().run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, MainScreen)
        chat = screen.query_one("#chat-content", ChatPanel)
        tp = TurnProvenance()
        tp.record_tool("run_sql", True, "")
        chat.last_turn_provenance = tp

        mgr = ThreadManager(store_dir=tmp_path / "threads_store")
        other = mgr.create_thread(name="other", profile_name=screen._profile)
        screen._thread_manager = mgr

        # action_manage_threads pushes ThreadManagerScreen and wires its result
        # to a private _on_result closure — intercept push_screen to invoke
        # that closure directly with a target thread id, bypassing the modal's
        # own UI (which isn't what this seam is testing).
        captured: list = []

        def _fake_push_screen(
            modal_screen: object, callback: object = None, **kwargs: object
        ) -> None:
            if callback is not None:
                captured.append(callback)

        monkeypatch.setattr(pilot.app, "push_screen", _fake_push_screen)

        screen.action_manage_threads()
        assert captured, "action_manage_threads did not push a screen with a callback"
        captured[0](other.id)

        assert screen._current_thread_id == other.id
        assert chat.last_turn_provenance is None


async def test_capture_finding_survives_store_error(tmp_path: Path, monkeypatch) -> None:
    """A data-store OSError degrades to a notify, never raises into Textual."""
    _redirect_cheese_roots(tmp_path, monkeypatch)

    def _boom(*a: object, **k: object) -> str:
        raise OSError("disk full")

    monkeypatch.setattr("labrat.cheese.store.FindingDataStore.capture", _boom)
    async with _MainHost().run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, MainScreen)
        screen._last_sql = "SELECT 1 AS x"
        screen._last_user_prompt = "q"
        screen.query_one("#results-content", ResultsTable).load(pl.DataFrame({"x": [1]}))
        finding = screen._capture_finding(question="q", sql="SELECT 1 AS x")
        assert finding is None  # degraded, did not raise
        await pilot.pause()


async def test_capture_finding_carries_turn_provenance(tmp_path: Path, monkeypatch) -> None:
    """A populated TurnProvenance snapshot is captured onto the pinned Finding."""
    from labrat.widgets.turn_provenance import TurnProvenance

    _redirect_cheese_roots(tmp_path, monkeypatch)
    async with _MainHost().run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, MainScreen)
        screen._last_sql = "SELECT 1 AS x"
        screen.query_one("#results-content", ResultsTable).load(pl.DataFrame({"x": [1]}))
        chat = screen.query_one("#chat-content", ChatPanel)
        tp = TurnProvenance()
        tp.record_tool("run_sql", True, "")
        chat.last_turn_provenance = tp
        finding = screen._capture_finding(question="q", sql="SELECT 1 AS x")
        assert finding is not None
        assert finding.provenance is not None
        assert finding.provenance.run_sql_count == 1
