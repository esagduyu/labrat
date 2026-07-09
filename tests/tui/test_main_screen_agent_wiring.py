"""Phase-1 wiring: TUI chat registry is a superset of the benchmark registry,
ToolContext carries read_only/profile, and the loop is factory-built."""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static

from labrat.agent.data_tools import build_data_tools_registry
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


async def test_chat_registry_superset_and_ctx_wiring(ecommerce_db: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # deterministic provider path
    screen = _connected_screen(ecommerce_db)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        loop = pilot.app.screen.query_one("#chat-content", ChatPanel)._agent_loop
        assert loop is not None
        tui_names = {t.name for t in loop._registry.tools}
        bench_names = {t.name for t in build_data_tools_registry().tools}
        assert bench_names <= tui_names  # benchmark superset
        for extra in (
            "draft_sql",
            "create_chart",
            "run_validations",
            "recall_memories",
            "search_query_history",
        ):
            assert extra in tui_names
        ctx = loop._ctx
        assert ctx.profile_name == "testprof"
        assert ctx.read_only is True  # from profile.is_read_only
        assert ctx.primary == "main" and "main" in ctx.connections
        assert ctx.llm_fn is not None  # factory injected
        assert loop._ledger is not None  # ledger attached


async def test_mount_without_connection_still_works() -> None:
    # Existing default-construction path (used by all current TUI tests) must not break.
    async with _Host(MainScreen()).run_test() as pilot:
        await pilot.pause()
        assert pilot.app.screen.query_one("#chat-pane") is not None
