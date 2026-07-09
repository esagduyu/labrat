"""First-connect Cartographer wiring on MainScreen."""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static

from labrat.db.duckdb_engine import DuckDBConnection
from labrat.profile.model import Profile
from labrat.screens.main import MainScreen


class _Host(App[None]):
    def __init__(self, screen: MainScreen) -> None:
        super().__init__()
        self._screen = screen

    def compose(self) -> ComposeResult:
        yield Static("")

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def _screen(ecommerce_db: Path, scent_dir: Path) -> MainScreen:
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    return MainScreen(
        profile="scentprof",
        dialect="duckdb",
        catalog=catalog,
        connection=conn,
        profile_obj=Profile(name="scentprof", dialect="duckdb", path=str(ecommerce_db)),
        scent_dir=scent_dir,
    )


async def test_mount_runs_prepass_into_scent_dir(
    ecommerce_db: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    scent_dir = tmp_path / "scent"
    async with _Host(_screen(ecommerce_db, scent_dir)).run_test() as pilot:
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
        assert any(scent_dir.glob("*.md"))
        assert (scent_dir / ".schema_fingerprint").exists()
        screen = pilot.app.screen
        assert screen._scent_stale is False


async def test_refresh_scent_regenerates(ecommerce_db: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    scent_dir = tmp_path / "scent"
    async with _Host(_screen(ecommerce_db, scent_dir)).run_test() as pilot:
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
        before = sorted(scent_dir.glob("*.md"))
        assert before
        # Simulate drift, then refresh via the action's confirmed path.
        (scent_dir / ".schema_fingerprint").write_text("stale\n")
        pilot.app.screen._do_refresh_scent()  # the post-confirm entry point
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
        assert sorted(scent_dir.glob("*.md"))  # regenerated
        assert (scent_dir / ".schema_fingerprint").read_text().strip() != "stale"
