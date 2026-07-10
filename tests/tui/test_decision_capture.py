"""Decision-trail v1: ctrl+shift+d capture -> immediate persist -> harvest-review wiring."""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static, TextArea

from labrat.db.duckdb_engine import DuckDBConnection
from labrat.memory.model import MemoryKind, MemoryScope
from labrat.memory.store import MemoryStore
from labrat.profile.model import Profile
from labrat.screens.harvest_review import HarvestReviewScreen
from labrat.screens.main import MainScreen
from labrat.screens.record_decision import RecordDecisionScreen


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
        profile="dprof",
        dialect="duckdb",
        catalog=conn.introspect_catalog(),
        connection=conn,
        profile_obj=Profile(
            name="dprof", dialect="duckdb", path=str(ecommerce_db), harvest_opt_in=opt_in
        ),
    )


def _isolate_memory_dir(monkeypatch, tmp_path: Path) -> Path:
    import labrat.memory.store as memory_store_mod

    memory_dir = tmp_path / "memories"
    monkeypatch.setattr(memory_store_mod, "_DEFAULT_MEMORY_DIR", memory_dir)
    return memory_dir


async def test_record_decision_gated_off(ecommerce_db: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    memory_dir = _isolate_memory_dir(monkeypatch, tmp_path)

    screen = _screen(ecommerce_db, opt_in=False)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()

        await pilot.press("ctrl+shift+d")
        await pilot.pause()
        # harvest_opt_in defaults False -> action notifies and pushes nothing.
        assert pilot.app.screen is screen

    # Nothing recorded — the memory file was never even created.
    assert not memory_dir.exists() or MemoryStore(memory_dir).read_profile("dprof") == []


async def test_record_decision_persists_and_harvests(
    ecommerce_db: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    memory_dir = _isolate_memory_dir(monkeypatch, tmp_path)

    screen = _screen(ecommerce_db, opt_in=True)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()

        await pilot.press("ctrl+shift+d")
        await pilot.pause()
        modal = pilot.app.screen
        assert isinstance(modal, RecordDecisionScreen)

        modal.query_one(
            "#decision-text", TextArea
        ).text = "Always exclude test orders from revenue metrics."
        await pilot.press("ctrl+s")
        await pilot.pause()
        # Modal dismissed back to MainScreen.
        assert pilot.app.screen is screen

        # Immediate, durable persist — a explicit_user_rule Memory landed in the store.
        memories = MemoryStore(memory_dir).read_profile("dprof")
        assert len(memories) == 1
        recorded = memories[0]
        assert recorded.kind == MemoryKind.explicit_user_rule
        assert recorded.scope == MemoryScope.global_
        assert recorded.text == "Always exclude test orders from revenue metrics."

        # Harvest review surfaces a Decisions draft alongside (absent) Gotchas.
        worker = screen._run_harvest_review()
        await worker.wait()
        await pilot.pause()
        review = pilot.app.screen
        assert isinstance(review, HarvestReviewScreen)
        assert any(
            section.heading == "Decisions" and "exclude test orders" in section.body
            for _key, section in review._rows
        )

        # Approve -> writes to the domain's Scent doc (pops back to MainScreen).
        await pilot.click("#apply-btn")
        await pilot.pause()
        assert pilot.app.screen is screen

    doc_path = tmp_path / "labrat_maze" / "scent" / "general.md"
    assert doc_path.is_file()
    text = doc_path.read_text()
    assert "## Decisions" in text
    assert "Always exclude test orders from revenue metrics." in text
    assert "**Source:** harvested" in text
