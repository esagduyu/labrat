"""Findings-viewer cheese exports + version browser rollback."""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static

from labrat.cheese.model import CheeseManifest
from labrat.screens.findings_viewer import FindingsViewerScreen
from labrat.screens.main import MainScreen


class _MainHost(App[None]):
    """Minimal app that pushes MainScreen for testing (mirrors test_cheese_capture.py)."""

    def compose(self) -> ComposeResult:
        yield Static("")

    def on_mount(self) -> None:
        self.push_screen(MainScreen())


async def test_viewer_report_export_and_single_export(tmp_path: Path, monkeypatch) -> None:
    import labrat.cheese.store as cheese_store_mod
    import labrat.thread.findings as findings_mod
    from labrat.thread.findings import FindingsManager

    monkeypatch.setattr(cheese_store_mod, "DEFAULT_DATA_ROOT", tmp_path / "data")
    monkeypatch.setattr(cheese_store_mod, "DEFAULT_CHEESE_ROOT", tmp_path / "cheese")
    # FindingsViewerScreen builds its own FindingsManager() with no store_dir — point
    # its default at tmp_path too, so it reads the two findings pinned below.
    monkeypatch.setattr(findings_mod, "_DEFAULT_DIR", tmp_path)

    mgr = FindingsManager(store_dir=tmp_path)  # and point the viewer at it per fixture pattern
    mgr.pin(version_id="v", question="q1", sql="s1", results_ref=None, chart_spec=None)
    mgr.pin(version_id="v", question="q2", sql="s2", results_ref=None, chart_spec=None)

    async with _MainHost().run_test() as pilot:
        await pilot.pause()
        screen = pilot.app.screen
        assert isinstance(screen, MainScreen)

        await pilot.press("ctrl+k")
        await pilot.pause()
        viewer = pilot.app.screen
        assert isinstance(viewer, FindingsViewerScreen)

        await pilot.press("e")  # report export: all pinned findings
        await pilot.pause()
        await pilot.press("x")  # single export: the row-0 (default cursor) finding
        await pilot.pause()

    cheese_root = tmp_path / "cheese"
    cheese_dirs = [d for d in cheese_root.iterdir() if d.is_dir()]
    assert len(cheese_dirs) == 2

    manifests = [
        CheeseManifest.model_validate_json((d / "manifest.json").read_text()) for d in cheese_dirs
    ]
    kinds = {m.kind for m in manifests}
    assert kinds == {"report", "single"}

    report = next(m for m in manifests if m.kind == "report")
    assert len(report.finding_ids) == 2

    single = next(m for m in manifests if m.kind == "single")
    assert len(single.finding_ids) == 1


async def test_versions_screen_lists_and_rolls_back(tmp_path: Path, monkeypatch) -> None:
    import labrat.cheese.store as cheese_store_mod
    from labrat.cheese.store import CheeseStore
    from labrat.screens.cheese_versions import CheeseVersionsScreen

    monkeypatch.setattr(cheese_store_mod, "DEFAULT_CHEESE_ROOT", tmp_path / "cheese")
    cs = CheeseStore(tmp_path / "cheese")
    m = cs.create_or_get("single", ["f1"], "My insight")
    cs.add_version(m.cheese_id, "<html>v1</html>", "preview")
    cs.add_version(m.cheese_id, "<html>v2</html>", "preview")

    async with _MainHost().run_test() as pilot:
        await pilot.pause()
        pilot.app.push_screen(CheeseVersionsScreen())
        await pilot.pause()
        modal = pilot.app.screen
        assert isinstance(modal, CheeseVersionsScreen)
        assert modal._rows == [(m.cheese_id, 1), (m.cheese_id, 2)]

        from textual.widgets import ListView

        list_view = modal.query_one("#versions-list", ListView)
        list_view.index = 0  # select v1
        await pilot.press("r")  # rollback to v1
        await pilot.pause()

    got = cs.get(m.cheese_id)
    assert got is not None and got.current == 1


def test_rollback_via_store_semantics(tmp_path: Path) -> None:
    # The modal's rollback action delegates to CheeseStore.rollback — pin the
    # delegation with a unit-level test if pilot-driving the modal is brittle:
    from labrat.cheese.store import CheeseStore

    cs = CheeseStore(tmp_path)
    m = cs.create_or_get("single", ["f1"], "t")
    cs.add_version(m.cheese_id, "a", "preview")
    cs.add_version(m.cheese_id, "b", "preview")
    cs.rollback(m.cheese_id, 1)
    got = cs.get(m.cheese_id)
    assert got is not None and got.current == 1
