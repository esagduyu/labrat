"""Main three-pane layout screen for LabRat."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import polars as pl
from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widget import Widget

if TYPE_CHECKING:
    from labrat.db.base import Connection
    from labrat.db.catalog import Catalog
    from labrat.profile.model import Profile


class _StatusBar(Widget):
    """One-row status bar: profile | dialect | thread | connection."""

    DEFAULT_CSS = """
    _StatusBar {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        *,
        profile: str = "—",
        dialect: str = "—",
        thread: str = "—",
        connected: bool = False,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._profile = profile
        self._dialect = dialect
        self._thread = thread
        self._connected = connected

    def set_thread(self, name: str) -> None:
        self._thread = name
        self.refresh()

    def render(self) -> str:
        status = "● connected" if self._connected else "○ disconnected"
        return (
            f" profile: {self._profile}"
            f"  dialect: {self._dialect}"
            f"  thread: {self._thread}"
            f"  {status}"
            f"  \U0001f400 LabRat"
        )


class _PaneHeader(Widget):
    """Single-row title strip for a pane, with an optional right-aligned hint."""

    DEFAULT_CSS = """
    _PaneHeader {
        height: 1;
        background: $panel-darken-1;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(self, title: str, hint: str = "", id: str | None = None) -> None:
        super().__init__(id=id)
        self._title = title
        self._hint = hint

    def render(self) -> object:
        from rich.text import Text

        t = Text(f"─ {self._title} ─")
        if self._hint:
            t.append(f"  {self._hint}", style="dim")
        return t


class _PaneDivider(Widget):
    """Draggable 1-column handle between adjacent horizontal panes."""

    DEFAULT_CSS = """
    _PaneDivider {
        width: 1;
        height: 1fr;
        background: $surface-darken-1;
    }
    _PaneDivider:hover {
        background: $accent-darken-1;
    }
    """

    def __init__(self, pane_id: str, sign: int = 1, min_pane_width: int = 15) -> None:
        super().__init__()
        self._pane_id = pane_id
        self._sign = sign
        self._min_pane_width = min_pane_width
        self._dragging = False
        self._start_x = 0
        self._start_width = 0

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._dragging = True
        self._start_x = event.screen_x
        self._start_width = self.screen.query_one(f"#{self._pane_id}").size.width
        self.capture_mouse()
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if not self._dragging:
            return
        dx = event.screen_x - self._start_x
        pane = self.screen.query_one(f"#{self._pane_id}")
        pane.styles.width = max(self._min_pane_width, self._start_width + self._sign * dx)
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        self._dragging = False
        self.release_mouse()
        event.stop()

    def render(self) -> str:
        return ""


class MainScreen(Screen[None]):
    """Three-pane layout: chat | editor + results | schema browser."""

    CSS_PATH = "../styles.tcss"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+1", "focus_chat", "Chat"),
        Binding("ctrl+2", "focus_editor", "Editor"),
        Binding("ctrl+3", "focus_results", "Results"),
        Binding("ctrl+4", "focus_schema", "Schema"),
        Binding("ctrl+h", "toggle_schema", "Toggle Schema"),
        Binding("ctrl+l", "toggle_chat", "Toggle Chat"),
        Binding("question_mark,f1", "show_help", "Help", show=True),
        Binding("ctrl+t", "manage_threads", "Threads", show=True),
        Binding("ctrl+k", "view_findings", "Findings", show=True),
        Binding("ctrl+r", "view_history", "History", show=True),
        Binding("ctrl+g", "view_memories", "Memories", show=True),
        Binding("ctrl+backslash", "toggle_traces", "Traces", show=False),
    ]

    def __init__(
        self,
        *,
        profile: str = "—",
        dialect: str = "—",
        thread: str = "untitled",
        catalog: Catalog | None = None,
        connection: Connection | None = None,
        profile_obj: Profile | None = None,
    ) -> None:
        super().__init__()
        self._profile = profile
        self._dialect = dialect
        self._thread = thread
        self._catalog = catalog
        self._connection = connection
        self._profile_obj = profile_obj
        self._current_thread_id: str | None = None
        self._current_thread_name: str = "untitled"
        self._last_sql: str = ""
        self._agent_loop = None
        self._provider = None

    def compose(self) -> ComposeResult:
        connected = self._connection is not None
        yield _StatusBar(
            profile=self._profile,
            dialect=self._dialect,
            thread=self._thread,
            connected=connected,
            id="status-top",
        )
        with Horizontal(id="main-split"):
            with Vertical(id="chat-pane"):
                yield _PaneHeader("chat")
                from labrat.widgets.chat_panel import ChatPanel

                yield ChatPanel(id="chat-content")
            yield _PaneDivider("chat-pane", sign=1, min_pane_width=20)
            with Vertical(id="center-pane"):
                with Vertical(id="editor-pane"):
                    yield _PaneHeader("editor", hint="Ctrl+Enter: Run")
                    from labrat.widgets.query_editor import QueryEditor

                    yield QueryEditor(id="editor-content")
                with Vertical(id="results-pane"):
                    yield _PaneHeader("results", id="results-header")
                    from textual.widgets import LoadingIndicator, RichLog

                    from labrat.widgets.results_table import ResultsTable

                    yield LoadingIndicator(id="sql-loading")
                    yield ResultsTable(id="results-content")
                    yield RichLog(id="chart-content", highlight=False, wrap=False, markup=True)
            yield _PaneDivider("schema-pane", sign=-1, min_pane_width=15)
            with Vertical(id="schema-pane"):
                yield _PaneHeader("schema")
                from labrat.widgets.schema_tree import SchemaBrowser

                browser = SchemaBrowser(
                    catalog=self._catalog,
                    profile_name=self._profile,
                    id="schema-content",
                )
                if self._connection is not None:
                    browser.set_connection(self._connection)
                yield browser
        yield _StatusBar(
            profile=self._profile,
            dialect=self._dialect,
            thread=self._thread,
            connected=connected,
            id="status-bottom",
        )

    def on_mount(self) -> None:
        from textual.widgets import LoadingIndicator, RichLog

        from labrat.thread.findings import FindingsManager
        from labrat.thread.manager import ThreadManager
        from labrat.widgets.results_table import ResultsTable

        # Chart log and loading indicator hidden initially.
        self.query_one("#chart-content", RichLog).display = False
        self.query_one("#sql-loading", LoadingIndicator).display = False

        # Thread + findings managers (always available, regardless of connection).
        self._thread_manager = ThreadManager()
        self._findings_manager = FindingsManager()
        threads = [
            t for t in self._thread_manager.list_threads() if t.profile_name == self._profile
        ]
        if threads:
            t = threads[-1]
        else:
            t = self._thread_manager.create_thread(name="untitled", profile_name=self._profile)
        self._current_thread_id = t.id
        self._current_thread_name = t.name
        for bar in self.query(_StatusBar):
            bar.set_thread(t.name)

        if self._connection is None:
            return

        from rich.text import Text

        from labrat.agent.data_tools import build_data_tools_registry
        from labrat.agent.prompts import build_tui_system_prompt
        from labrat.agent.session import build_agent_session, resolve_provider
        from labrat.agent.tools.base import ToolContext
        from labrat.agent.tools.create_chart import CreateChartTool
        from labrat.agent.tools.draft_sql import DraftSqlTool
        from labrat.agent.tools.recall_memories import RecallMemoriesTool
        from labrat.agent.tools.run_sql import RunSqlTool
        from labrat.agent.tools.run_validations import RunValidationsTool
        from labrat.agent.tools.search_query_history import SearchQueryHistoryTool
        from labrat.profile.model import Profile
        from labrat.widgets.chat_panel import ChatPanel
        from labrat.widgets.query_editor import QueryEditor

        editor = self.query_one("#editor-content", QueryEditor)
        table = self.query_one("#results-content", ResultsTable)
        chart_log = self.query_one("#chart-content", RichLog)

        def on_draft(sql: str) -> None:
            editor.load_text(sql)
            self._last_sql = sql

        def on_result(df: pl.DataFrame, elapsed_ms: float) -> None:
            table.load(df, execution_time=elapsed_ms)
            table.display = True
            chart_log.display = False

        def on_chart(chart_str: str) -> None:
            chart_log.clear()
            chart_log.write(Text.from_ansi(chart_str))
            chart_log.display = True
            table.display = False

        profile_obj = self._profile_obj or Profile(
            name=self._profile if self._profile != "—" else "default",
            dialect=self._dialect if self._dialect != "—" else "duckdb",
        )

        registry = build_data_tools_registry(
            run_sql_tool=RunSqlTool(on_result=on_result, on_draft=on_draft)
        )
        registry.register(DraftSqlTool(on_draft=on_draft))
        registry.register(CreateChartTool(on_chart=on_chart))
        registry.register(RunValidationsTool())
        registry.register(RecallMemoriesTool())
        registry.register(SearchQueryHistoryTool())

        catalogs: dict[str, object] = {}
        if self._catalog is not None:
            catalogs["main"] = self._catalog
        ctx = ToolContext(
            connections={"main": self._connection},
            catalogs=catalogs,
            primary="main",
            profile_name=profile_obj.name,
            read_only=profile_obj.is_read_only,
        )

        provider, degraded_warning = resolve_provider(profile_obj)
        if degraded_warning:
            self.notify(degraded_warning, severity="warning", timeout=8)
        self._provider = provider

        import time as _time
        from pathlib import Path as _Path

        ledger_dir = _Path.home() / ".labrat" / "ledger" / profile_obj.name / str(int(_time.time()))
        loop = build_agent_session(
            ctx=ctx,
            registry=registry,
            provider=provider,
            system_prompt=build_tui_system_prompt(
                self._dialect if self._dialect != "—" else "duckdb"
            ),
            dialect=self._dialect if self._dialect != "—" else "duckdb",
            verify=profile_obj.verify_enabled,
            enable_ledger=True,
            ledger_dir=ledger_dir,
        )
        self._agent_loop = loop
        self.query_one("#chat-content", ChatPanel).set_agent_loop(loop)

        # Wire SQL autocomplete into the editor.
        from labrat.sql.completer import SQLCompleter

        completer = SQLCompleter(catalog=self._catalog)
        editor.set_completer(completer)

    def on_resize(self, event: events.Resize) -> None:
        """Enter narrow mode below 80 columns."""
        if event.size.width < 80:
            self.add_class("narrow")
        else:
            self.remove_class("narrow")

    # ── SQL run handler ───────────────────────────────────────────────────────

    def on_query_editor_run_requested(self, event: object) -> None:
        from labrat.agent.tools.run_sql import _is_mutation
        from labrat.widgets.query_editor import QueryEditor

        if not isinstance(event, QueryEditor.RunRequested):
            return
        if self._connection is None:
            return
        sql = event.sql
        if _is_mutation(sql):
            from labrat.screens.confirm import ConfirmScreen

            def _after_confirm(confirmed: bool | None) -> None:
                if confirmed:
                    self._execute_sql(sql)

            self.app.push_screen(
                ConfirmScreen(
                    "[bold yellow]⚠ Mutation detected[/bold yellow]\n\n"
                    f"[dim]{sql[:120]}{'…' if len(sql) > 120 else ''}[/dim]\n\n"
                    "This statement modifies data. Run anyway?"
                ),
                _after_confirm,
            )
        else:
            self._execute_sql(sql)

    @work(exclusive=True)
    async def _execute_sql(self, sql: str) -> None:
        import asyncio
        import time

        from textual.widgets import LoadingIndicator, RichLog

        from labrat.widgets.results_table import ResultsTable

        loading = self.query_one("#sql-loading", LoadingIndicator)
        table = self.query_one("#results-content", ResultsTable)
        chart_log = self.query_one("#chart-content", RichLog)
        loading.display = True
        table.display = False
        chart_log.display = False
        chart_log.clear()
        try:
            assert self._connection is not None
            t0 = time.monotonic()
            df = await asyncio.to_thread(self._connection.execute, sql)
            elapsed_ms = (time.monotonic() - t0) * 1000
            self._last_sql = sql
            table.load(df, execution_time=elapsed_ms)
            table.display = True
        except Exception as exc:
            chart_log.write(f"[bold red]SQL error:[/bold red] {exc}")
            chart_log.display = True
            table.display = False
        finally:
            loading.display = False

    # ── actions ──────────────────────────────────────────────────────────────

    # ── pin finding handler (M18) ─────────────────────────────────────────────

    def on_results_table_pin_requested(self) -> None:
        sql = self._last_sql
        if not sql:
            self.notify("Run a query first before pinning.", severity="warning")
            return
        from labrat.thread.findings import FindingsManager

        mgr = FindingsManager()
        mgr.pin(
            version_id=self._current_thread_id or "unknown",
            question="Pinned from results table",
            sql=sql,
            results_ref=None,
            chart_spec=None,
            note="",
        )
        self.notify("Finding pinned!  Press Ctrl+K to view findings.", timeout=3)

    # ── new modal actions ─────────────────────────────────────────────────────

    def action_manage_threads(self) -> None:
        from labrat.screens.thread_manager import ThreadManagerScreen

        def _on_result(thread_id: str | None) -> None:
            if not thread_id:
                return
            t = self._thread_manager.get_thread(thread_id)
            if t is None:
                return
            self._current_thread_id = t.id
            self._current_thread_name = t.name
            for bar in self.query(_StatusBar):
                bar.set_thread(t.name)

        self.app.push_screen(
            ThreadManagerScreen(
                profile_name=self._profile,
                current_thread_id=self._current_thread_id,
            ),
            _on_result,
        )

    def action_view_findings(self) -> None:
        from labrat.screens.findings_viewer import FindingsViewerScreen

        self.app.push_screen(FindingsViewerScreen())

    def action_view_history(self) -> None:
        from labrat.screens.history_browser import HistoryBrowserScreen

        def _on_result(sql: str | None) -> None:
            if sql:
                from labrat.widgets.query_editor import QueryEditor

                self.query_one("#editor-content", QueryEditor).load_text(sql)

        self.app.push_screen(HistoryBrowserScreen(profile_name=self._profile), _on_result)

    def action_view_memories(self) -> None:
        from labrat.screens.memories_viewer import MemoriesViewerScreen

        self.app.push_screen(MemoriesViewerScreen(profile_name=self._profile))

    def action_toggle_traces(self) -> None:
        from labrat.widgets.chat_panel import ChatPanel

        self.query_one("#chat-content", ChatPanel).toggle_traces()

    def action_show_help(self) -> None:
        from labrat.screens.help import HelpScreen

        self.app.push_screen(HelpScreen())

    def action_focus_chat(self) -> None:
        from labrat.widgets.chat_panel import ChatPanel

        self.query_one("#chat-content", ChatPanel).query_one("#user-input").focus()

    def action_focus_editor(self) -> None:
        from labrat.widgets.query_editor import QueryEditor

        self.query_one("#editor-content", QueryEditor).focus()

    def action_focus_results(self) -> None:
        from labrat.widgets.results_table import ResultsTable

        self.query_one("#results-content", ResultsTable).focus()

    def action_focus_schema(self) -> None:
        self.query_one("#schema-content").query_one("#schema-tree").focus()

    def action_toggle_schema(self) -> None:
        schema = self.query_one("#schema-pane")
        schema.display = not schema.display

    def action_toggle_chat(self) -> None:
        chat = self.query_one("#chat-pane")
        chat.display = not chat.display
