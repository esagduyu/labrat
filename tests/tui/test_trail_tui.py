"""Save-as-Trail: gated draft -> review -> audited apply (Trail v1, Task 3)."""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static

from labrat.db.duckdb_engine import DuckDBConnection
from labrat.profile.model import Profile
from labrat.screens.findings_viewer import FindingsViewerScreen
from labrat.screens.main import MainScreen
from labrat.screens.trail_review import TrailReviewScreen


class _Host(App[None]):
    """Minimal app that pushes MainScreen for testing (mirrors test_main_screen_harvest.py)."""

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
        profile="tprof",
        dialect="duckdb",
        catalog=conn.introspect_catalog(),
        connection=conn,
        profile_obj=Profile(
            name="tprof", dialect="duckdb", path=str(ecommerce_db), trail_opt_in=opt_in
        ),
    )


def _pin_finding(tmp_path: Path, monkeypatch) -> None:
    import labrat.thread.findings as findings_mod
    from labrat.thread.findings import FindingsManager

    # FindingsViewerScreen builds its own FindingsManager() with no store_dir —
    # point its default at tmp_path (same seam as test_cheese_versions.py).
    monkeypatch.setattr(findings_mod, "_DEFAULT_DIR", tmp_path)
    mgr = FindingsManager(store_dir=tmp_path)
    mgr.pin(
        version_id="v1",
        question="How many orders per customer?",
        sql="SELECT customer_id, count(*) FROM orders GROUP BY customer_id",
        results_ref=None,
        chart_spec=None,
    )


async def test_save_as_trail_gated_off_by_default(
    ecommerce_db: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    _pin_finding(tmp_path, monkeypatch)

    screen = _screen(ecommerce_db, opt_in=False)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()

        await pilot.press("ctrl+k")
        await pilot.pause()
        viewer = pilot.app.screen
        assert isinstance(viewer, FindingsViewerScreen)

        # trail_opt_in defaults False -> action notifies and writes nothing.
        await pilot.press("t")
        await pilot.pause()
        # Gated off: no TrailReviewScreen pushed, viewer stays on top.
        assert pilot.app.screen is viewer

    assert not (tmp_path / "labrat_maze" / "trail").exists()


async def test_save_as_trail_writes_when_opted_in(
    ecommerce_db: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    _pin_finding(tmp_path, monkeypatch)

    screen = _screen(ecommerce_db, opt_in=True)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()

        await pilot.press("ctrl+k")
        await pilot.pause()
        viewer = pilot.app.screen
        assert isinstance(viewer, FindingsViewerScreen)

        await pilot.press("t")
        await pilot.pause()
        review = pilot.app.screen
        assert isinstance(review, TrailReviewScreen)

        await pilot.press("a")  # approve -> apply_trail -> pop back to the viewer
        await pilot.pause()
        assert pilot.app.screen is viewer

    trail_dir = tmp_path / "labrat_maze" / "trail"
    assert trail_dir.is_dir()
    files = list(trail_dir.glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text()
    assert "kind: trail" in text
    assert "how-many-orders-per-customer" in files[0].stem
