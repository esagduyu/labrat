"""First-connect dbt semantic ingestion wiring on MainScreen."""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static

from labrat.db.duckdb_engine import DuckDBConnection
from labrat.profile.model import Profile
from labrat.screens.main import MainScreen

_FIXTURE = Path("tests/fixtures/sample_dbt_project/manifest_semantic.json")


class _Host(App[None]):
    def __init__(self, screen: MainScreen) -> None:
        super().__init__()
        self._screen = screen

    def compose(self) -> ComposeResult:
        yield Static("")

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def _screen(ecommerce_db: Path, tmp_path: Path, *, dbt: bool) -> MainScreen:
    conn = DuckDBConnection(path=str(ecommerce_db), read_only=True)
    conn.connect()
    return MainScreen(
        profile="semprof",
        dialect="duckdb",
        catalog=conn.introspect_catalog(),
        connection=conn,
        profile_obj=Profile(
            name="semprof",
            dialect="duckdb",
            path=str(ecommerce_db),
            dbt_project_path="/configured" if dbt else None,
        ),
        scent_dir=tmp_path / "scent",
        dbt_manifest_override=_FIXTURE if dbt else None,
        project_root_override=tmp_path / "proj",
    )


async def test_mount_ingests_when_dbt_configured(
    ecommerce_db: Path, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    async with _Host(_screen(ecommerce_db, tmp_path, dbt=True)).run_test() as pilot:
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
        scent = tmp_path / "proj" / "labrat_maze" / "scent"
        assert (scent / "orders.md").exists()
        assert (scent / ".manifest_fingerprint").exists()
        text = (scent / "orders.md").read_text(encoding="utf-8")
        assert "**Source:** semantic_layer" in text
        assert "**Meta:** schema_hash=" in text  # catalog stamped


async def test_no_dbt_path_no_ingest(ecommerce_db: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    async with _Host(_screen(ecommerce_db, tmp_path, dbt=False)).run_test() as pilot:
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
        assert not (tmp_path / "proj" / "labrat_maze").exists()
