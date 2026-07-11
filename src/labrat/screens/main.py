"""Main three-pane layout screen for LabRat."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import polars as pl
from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widget import Widget

if TYPE_CHECKING:
    from labrat.agent.tools.base import SubagentRunner
    from labrat.chart.spec import ChartSpec
    from labrat.db.base import Connection
    from labrat.db.catalog import Catalog
    from labrat.maze.store import MazeStore
    from labrat.profile.model import Profile
    from labrat.thread.model import Finding


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
        self._active_maps: list[str] = []

    def set_thread(self, name: str) -> None:
        self._thread = name
        self.refresh()

    def set_active_maps(self, maps: list[str]) -> None:
        """Display copy of the active-Maps set. Mirrors ``set_thread``; takes a
        copy for rendering — never rebinds the caller's live list."""
        self._active_maps = list(maps)
        self.refresh()

    def render(self) -> str:
        status = "● connected" if self._connected else "○ disconnected"
        maps_segment = ""
        if self._active_maps:
            shown = self._active_maps[:3]
            extra = len(self._active_maps) - len(shown)
            names = ", ".join(shown)
            if extra > 0:
                names += f", +{extra}"
            maps_segment = f"  \U0001f5fa Maps: {names}"
        return (
            f" profile: {self._profile}"
            f"  dialect: {self._dialect}"
            f"  thread: {self._thread}"
            f"  {status}"
            f"  \U0001f400 LabRat"
            f"{maps_segment}"
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
        Binding("ctrl+comma", "open_settings", "Settings", show=True),
        Binding("ctrl+shift+m", "refresh_scent", "Refresh Scent", show=False),
        Binding("ctrl+shift+h", "harvest_review", "Harvest", show=False),
        Binding("ctrl+shift+d", "record_decision", "Record Decision", show=False),
        Binding("ctrl+shift+p", "manage_maps", "Maps", show=True),
        Binding("f9", "reingest_semantics", "Re-ingest dbt", show=False),
        Binding("f8", "share_answer", "Share", show=True),
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
        scent_dir: Path | None = None,
        dbt_manifest_override: Path | None = None,
        project_root_override: Path | None = None,
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
        self._last_user_prompt: str = ""
        self._last_chart: tuple[ChartSpec, pl.DataFrame] | None = None
        self._agent_loop = None
        self._provider = None
        self._scent_dir_override = scent_dir
        self._scent_stale = False
        self._scent_dir: Path | None = None
        self._dbt_manifest_override = dbt_manifest_override
        self._project_root_override = project_root_override
        self._semantic_drifted = False
        # Map v1 activation set (TUI-only; NEVER set on the benchmark/eval/mcp
        # paths). This exact list object is handed to ToolContext below — mutate
        # in place (append/remove), never reassign, or the live link the search
        # tools read (ctx.active_maps) breaks.
        self._active_maps: list[str] = []
        # First-connect nudge (v1.1): fires at most once per screen instance.
        self._map_nudge_shown = False

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

    def _wrap_subagent_runner(self, inner: SubagentRunner) -> SubagentRunner:
        """Protect the M3 draft-capture baseline (_last_draft_sql/_last_sql)
        around a sub-agent dispatch (session-followups R3/P2). The TUI's SQL
        tools are shared instances, so a sub-agent's on_draft would otherwise
        overwrite the parent's baseline mid-run; snapshot before, restore
        after — even if the sub-run raises. Editor/pane updates during the
        sub-run remain live; only the capture baseline is protected."""

        async def wrapped(**kwargs: object) -> tuple[str, int, int]:
            saved_draft, saved_sql = self._last_draft_sql, self._last_sql
            try:
                return await inner(**kwargs)  # type: ignore[arg-type]
            finally:
                self._last_draft_sql, self._last_sql = saved_draft, saved_sql

        return wrapped  # type: ignore[return-value]

    def on_mount(self) -> None:
        from textual.widgets import LoadingIndicator, RichLog

        from labrat.memory.correction_buffer import CorrectionBuffer
        from labrat.thread.findings import FindingsManager
        from labrat.thread.manager import ThreadManager
        from labrat.widgets.results_table import ResultsTable

        # Chart log and loading indicator hidden initially.
        self.query_one("#chart-content", RichLog).display = False
        self.query_one("#sql-loading", LoadingIndicator).display = False

        # Correction capture buffer (M5 T2b surface) — pure bookkeeping, no LLM calls.
        self._correction_buffer = CorrectionBuffer()
        self._last_draft_sql: str | None = None
        self._harvesting: bool = False

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
            self._last_draft_sql = sql

        def on_result(df: pl.DataFrame, elapsed_ms: float) -> None:
            table.load(df, execution_time=elapsed_ms)
            table.display = True
            chart_log.display = False

        def on_chart(chart_str: str) -> None:
            chart_log.clear()
            chart_log.write(Text.from_ansi(chart_str))
            chart_log.display = True
            table.display = False

        def on_spec(spec: ChartSpec, df: pl.DataFrame) -> None:
            self._last_chart = (spec, df)

        profile_obj = self._profile_obj or Profile(
            name=self._profile if self._profile != "—" else "default",
            dialect=self._dialect if self._dialect != "—" else "duckdb",
        )

        registry = build_data_tools_registry(
            run_sql_tool=RunSqlTool(on_result=on_result, on_draft=on_draft)
        )
        registry.register(DraftSqlTool(on_draft=on_draft))
        registry.register(CreateChartTool(on_chart=on_chart, on_spec=on_spec))
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
            active_maps=self._active_maps,
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
        if ctx.subagent_runner is not None:
            ctx.subagent_runner = self._wrap_subagent_runner(ctx.subagent_runner)
        self._agent_loop = loop
        chat_panel = self.query_one("#chat-content", ChatPanel)
        chat_panel.set_agent_loop(loop)
        chat_panel.set_scent_stale_provider(lambda: self._scent_stale)

        # Wire SQL autocomplete into the editor.
        from labrat.sql.completer import SQLCompleter

        completer = SQLCompleter(catalog=self._catalog)
        editor.set_completer(completer)

        # First-connect Cartographer pre-pass (T2c) — runs after agent wiring so
        # chat is usable immediately regardless of pre-pass timing.
        from labrat.maze.store import user_scent_dir

        self._scent_dir = self._scent_dir_override or user_scent_dir(profile_obj.name)
        self._run_scent_prepass()
        self._run_semantic_ingest()

    def on_resize(self, event: events.Resize) -> None:
        """Enter narrow mode below 80 columns."""
        if event.size.width < 80:
            self.add_class("narrow")
        else:
            self.remove_class("narrow")

    # ── Cartographer scent (T2c) ────────────────────────────────────────────

    @work(exclusive=True, group="scent")
    async def _run_scent_prepass(self) -> None:
        if self._connection is None or self._catalog is None or self._scent_dir is None:
            return
        try:
            from labrat.maze.first_connect import tui_first_connect_prepass

            self.notify("\U0001f5fa mapping schema (Cartographer)…", timeout=3)
            outcome = await tui_first_connect_prepass(
                connections={"main": self._connection},
                catalogs={"main": self._catalog},
                primary="main",
                catalog=self._catalog,
                scent_dir=self._scent_dir,
            )
        except Exception as exc:  # fail-open: chat works without scent
            self.notify(
                f"Cartographer pre-pass failed (chat unaffected): {exc}",
                severity="warning",
                timeout=8,
            )
            return
        self._scent_stale = outcome.stale
        if outcome.generated:
            self.notify(f"scent ready · {len(outcome.doc_paths)} docs", timeout=4)
        elif outcome.stale:
            self.notify(
                "schema changed since scent was mapped — refresh with Ctrl+Shift+M",
                severity="warning",
                timeout=8,
            )
        self._maybe_nudge_map_seed()

    def _maybe_nudge_map_seed(self) -> None:
        """Point a dbt-configured, Map-less profile at the auto-seed action.
        Fires at most once per screen instance; never auto-runs the seed —
        the explicit-action decision stands."""
        if self._map_nudge_shown:
            return
        if self._profile_obj is None or not self._profile_obj.dbt_project_path:
            return
        if self._resolve_map_store().docs(kind="map"):
            return
        self._map_nudge_shown = True
        self.notify(
            "\U0001f5fa dbt project detected — press Ctrl+Shift+P → Auto-seed to "
            "sketch domain Maps",
            timeout=8,
        )

    # ── dbt semantic-layer ingestion (T1b) ──────────────────────────────────

    @work(exclusive=True, group="semantic")
    async def _run_semantic_ingest(self, *, force: bool = False) -> None:
        try:
            import os
            from pathlib import Path as _Path

            from labrat.maze.semantic_ingest import ingest_dbt_semantics
            from labrat.maze.store import MazeStore, project_scent_dir

            if self._profile_obj is None or not self._profile_obj.dbt_project_path:
                return
            if self._catalog is None:
                return
            manifest = self._dbt_manifest_override or (
                _Path(self._profile_obj.dbt_project_path) / "target" / "manifest.json"
            )
            if not manifest.is_file():
                self.notify(
                    f"dbt manifest not found — run `dbt parse` in your project ({manifest})",
                    severity="warning",
                    timeout=8,
                )
                return
            if self._project_root_override is not None:
                store = MazeStore(
                    project_root=self._project_root_override,
                    home=self._project_root_override / "home",
                    profile=self._profile_obj.name,
                )
                scent_dir = self._project_root_override / "labrat_maze" / "scent"
                git_root = self._project_root_override
            else:
                store = MazeStore.from_env(profile=self._profile_obj.name)
                scent_dir = project_scent_dir()
                # Mirrors MazeStore.from_env's project-root rule.
                git_root = _Path(os.environ.get("LABRAT_MAZE_DIR") or os.getcwd())
            outcome = ingest_dbt_semantics(
                manifest_path=manifest,
                catalog=self._catalog,
                store=store,
                project_scent_dir=scent_dir,
                force=force,
                git_root=git_root,
            )
            self._semantic_drifted = outcome.drifted
            if outcome.drifted:
                self.notify(
                    "dbt semantic layer changed — press F9 to re-ingest",
                    severity="warning",
                    timeout=8,
                )
            elif not outcome.skipped:
                self.notify(
                    f"semantic layer ingested · {outcome.sections_written} sections "
                    f"across {len(outcome.domains)} domains",
                    timeout=4,
                )
            elif outcome.cleared:
                self.notify(
                    f"cleared {outcome.cleared} stale semantic section(s) "
                    f"across {len(outcome.domains)} domains",
                    timeout=4,
                )
            for w in outcome.warnings[:3]:
                self.notify(f"dbt ingest: {w}", severity="warning", timeout=6)
        except Exception as exc:  # fail-open: chat unaffected; audit error surfaces here too
            self.notify(f"Semantic ingestion failed: {exc}", severity="error", timeout=8)

    def action_reingest_semantics(self) -> None:
        from labrat.screens.confirm import ConfirmScreen

        if self._profile_obj is None or not self._profile_obj.dbt_project_path:
            self.notify("No dbt project configured (Ctrl+, → dbt project).", severity="warning")
            return

        def _after(confirmed: bool | None) -> None:
            if confirmed:
                self._run_semantic_ingest(force=True)

        self.app.push_screen(
            ConfirmScreen(
                "[bold]Re-ingest dbt semantic layer?[/bold]\n\n"
                "Replaces semantic-layer sections in project Scent docs.\n"
                "[dim]Harvested and human sections are preserved.[/dim]"
            ),
            _after,
        )

    def action_refresh_scent(self) -> None:
        from labrat.screens.confirm import ConfirmScreen

        if self._scent_dir is None:
            self.notify("No scent store for this session.", severity="warning")
            return

        def _after(confirmed: bool | None) -> None:
            if confirmed:
                self._do_refresh_scent()

        self.app.push_screen(
            ConfirmScreen(
                "[bold]Regenerate scent?[/bold]\n\n"
                "Deletes and re-maps this profile's user-scope scent docs.\n"
                "[dim]Project-scope docs (incl. harvested sections) are untouched.[/dim]"
            ),
            _after,
        )

    def _do_refresh_scent(self) -> None:
        import shutil

        assert self._scent_dir is not None
        if self._scent_dir.exists():
            shutil.rmtree(self._scent_dir)  # user-scope Cartographer output only
        self._scent_stale = False
        self._run_scent_prepass()

    # ── Map v1 (domain bundles): author/curate + additive activation ────────

    def _resolve_map_store(self) -> MazeStore:
        """The MazeStore Map screens read/write. Mirrors ``_run_semantic_ingest``'s
        ``_project_root_override`` test-isolation branch (same home-dir rule) so
        tests can point every maze read/write at a tmp_path without env leakage."""
        from labrat.maze.store import MazeStore

        profile_name = self._profile_obj.name if self._profile_obj is not None else self._profile
        if self._project_root_override is not None:
            return MazeStore(
                project_root=self._project_root_override,
                home=self._project_root_override / "home",
                profile=profile_name,
            )
        return MazeStore.from_env(profile=profile_name)

    def _resolve_dbt_manifest_for_maps(self) -> Path | None:
        """Same manifest-path resolution as ``_run_semantic_ingest`` (dbt project
        configured + override wins), reused here for the Map auto-seed trigger."""
        if self._profile_obj is None or not self._profile_obj.dbt_project_path:
            return None
        manifest = self._dbt_manifest_override or (
            Path(self._profile_obj.dbt_project_path) / "target" / "manifest.json"
        )
        return manifest if manifest.is_file() else None

    def action_manage_maps(self) -> None:
        from labrat.screens.maps import MapActivateScreen

        store = self._resolve_map_store()
        self.app.push_screen(
            MapActivateScreen(store, self._active_maps, on_auto_seed=self.action_auto_seed_maps),
            lambda _=None: self._refresh_map_indicator(),
        )

    def _refresh_map_indicator(self) -> None:
        """Sync the status-bar Maps segment with the (possibly just-edited)
        ``_active_maps`` set. Mirrors the ``set_thread`` fan-out — the modal
        covers the screen while open, so refreshing on dismiss is sufficient."""
        for bar in self.query(_StatusBar):
            bar.set_active_maps(self._active_maps)

    def action_auto_seed_maps(self) -> int:
        """Cartographer dbt-structure auto-seed: sketch Map skeletons from the
        configured dbt project's folder structure, scent-member-filtered to
        domains that already have a Scent doc. Deterministic, no LLM; never
        raises into the TUI (fail-open on a missing/unreadable manifest,
        fail-loud-but-caught on a contamination hit). Returns the count of newly
        written Map skeletons (0 if none / on failure — always already notified)."""
        from datetime import UTC, datetime

        from labrat.maze.map import draft_maps_from_dbt
        from labrat.maze.scent_audit import ScentContaminationError

        manifest = self._resolve_dbt_manifest_for_maps()
        if manifest is None:
            self.notify(
                "No dbt manifest found — configure a dbt project first (Ctrl+,).",
                severity="warning",
            )
            return 0
        store = self._resolve_map_store()
        existing_scent = {d.domain for d in store.docs(kind="scent")}
        existing_maps = {d.domain for d in store.docs(kind="map")}
        model_id = self._profile_obj.agent_model if self._profile_obj is not None else None
        try:
            drafted = draft_maps_from_dbt(
                manifest,
                existing_scent_domains=existing_scent,
                generated_at=datetime.now(tz=UTC).date().isoformat(),
                model_id=model_id,
            )
        except ScentContaminationError as exc:
            self.notify(
                f"dbt Map auto-seed blocked by contamination audit: {exc}",
                severity="error",
                timeout=8,
            )
            return 0
        except Exception as exc:  # never raise into the TUI
            self.notify(f"dbt Map auto-seed failed: {exc}", severity="error", timeout=8)
            return 0
        new_docs = [doc for slug, doc in drafted.items() if slug not in existing_maps]
        for doc in new_docs:
            store.write_doc(doc, scope="project", kind="map")
        if new_docs:
            self.notify(
                f"\U0001f5fa sketched {len(new_docs)} domain Maps — curate + activate them",
                timeout=6,
            )
        else:
            self.notify("No new domain Maps to sketch (up to date).", timeout=4)
        return len(new_docs)

    # ── correction capture seams (M5 T2b / TUI-M3) ──────────────────────────

    def on_chat_panel_user_message(self, event: object) -> None:
        from labrat.widgets.chat_panel import ChatPanel

        if not isinstance(event, ChatPanel.UserMessage):
            return
        self._last_user_prompt = event.text
        self._last_chart = None
        if self._last_sql:
            self._correction_buffer.add_chat(event.text, self._last_sql)

    def _record_edit_if_diverged(self, executed_sql: str) -> None:
        if self._last_draft_sql is None:
            return
        self._correction_buffer.add_edit(
            profile=self._profile,
            thread_id=self._current_thread_id or "unknown",
            draft_sql=self._last_draft_sql,
            executed_sql=executed_sql,
        )
        self._last_draft_sql = None

    def action_harvest_review(self) -> None:
        from labrat.screens.harvest_controller import harvesting_enabled

        if self._profile_obj is None or self._provider is None:
            self.notify("Connect a profile first.", severity="warning")
            return
        if not harvesting_enabled(True, self._profile_obj.harvest_opt_in):
            self.notify("Harvesting is off — enable it in Settings (Ctrl+,).", severity="warning")
            return
        if self._harvesting:
            self.notify("Harvest already running…", timeout=3)
            return
        self._run_harvest_review()

    @work(exclusive=True, group="harvest")
    async def _run_harvest_review(self) -> None:
        from datetime import UTC, datetime

        from labrat.agent.verifier import provider_llm_fn
        from labrat.maze.scent_audit import ScentContaminationError
        from labrat.maze.store import MazeStore
        from labrat.memory.harvest import SessionHarvester
        from labrat.memory.store import MemoryStore
        from labrat.screens.harvest_controller import (
            harvesting_enabled,
            merge_drafts,
            review_corrections,
            review_decisions,
        )
        from labrat.screens.harvest_review import HarvestReviewScreen

        if self._harvesting:
            self.notify("Harvest already running…", timeout=3)
            return
        self._harvesting = True
        try:
            assert self._profile_obj is not None and self._provider is not None
            profile_name = self._profile_obj.name
            known_tables: list[str] = []
            if self._catalog is not None:
                known_tables = [t.name for s in self._catalog.schemas for t in s.tables]

            store = MemoryStore()
            harvester = SessionHarvester(
                profile_name,
                provider_llm_fn(self._provider),
                store,
                enabled=harvesting_enabled(True, self._profile_obj.harvest_opt_in),
                known_tables=known_tables,
            )
            chats, edits = self._correction_buffer.drain()
            self.notify(f"Harvesting {len(chats) + len(edits)} captured corrections…", timeout=3)
            try:
                for chat in chats:
                    await harvester.harvest_correction(chat.user_message, chat.context_sql)
                await harvester.harvest_events(edits)
            except Exception as exc:
                self._correction_buffer.restore(chats, edits)
                self.notify(f"Harvest failed: {exc}", severity="warning", timeout=8)
                return

            # Draft from the FULL memory store, not just this session: cancelled
            # reviews stay recoverable, and apply dedups so re-approval is idempotent.
            memories = store.read_profile(profile_name)
            maze_store = MazeStore.from_env(profile=profile_name)
            generated_at = datetime.now(tz=UTC).date().isoformat()
            model_id = getattr(self._provider, "_model", None)
            try:
                correction_drafts = review_corrections(
                    memories, generated_at=generated_at, model_id=model_id
                )
                # Analyst-recorded decisions (ctrl+shift+d) alongside harvested
                # corrections — review_decisions filters out decisions already
                # promoted into the domain's Decisions section.
                decision_drafts = review_decisions(
                    memories, maze_store, generated_at=generated_at, model_id=model_id
                )
            except ScentContaminationError as exc:
                self.notify(
                    f"Harvest drafts blocked by contamination audit: {exc}",
                    severity="error",
                    timeout=8,
                )
                return
            drafts = merge_drafts(correction_drafts, decision_drafts)
            if not drafts:
                self.notify("No new learnings to review.", timeout=4)
                return

            def _done(applied: int | None) -> None:
                if applied:
                    self.notify(f"Applied {applied} section(s) to Scent.", timeout=4)

            self.app.push_screen(HarvestReviewScreen(drafts, maze_store), _done)
        finally:
            self._harvesting = False

    def action_record_decision(self) -> None:
        from labrat.screens.harvest_controller import harvesting_enabled

        if self._profile_obj is None or not harvesting_enabled(
            True, self._profile_obj.harvest_opt_in
        ):
            self.notify(
                "Enable harvesting in Settings (Ctrl+,) to record decisions.",
                severity="warning",
            )
            return

        from labrat.screens.record_decision import RecordDecisionScreen

        def _on_result(raw_text: str | None) -> None:
            if raw_text is None:
                return
            # Normalize to single-line: the TextArea accepts Enter, and a
            # multi-line decision defeats filter_unpromoted_decisions (which
            # compares per-LINE bullets), causing it to re-draft forever and
            # eventually duplicate on disk once the promoted body diverges.
            # This also folds the empty/whitespace-only case in: "".split()
            # -> [] -> "" -> skip.
            text = " ".join(raw_text.split())
            if not text:
                return
            from labrat.memory.extractor import resolve_table_scope
            from labrat.memory.model import Memory, MemoryKind, MemoryScope
            from labrat.memory.store import MemoryStore

            known_tables: list[str] = []
            if self._catalog is not None:
                known_tables = [t.name for s in self._catalog.schemas for t in s.tables]
            memory = Memory(
                profile=self._profile,
                scope=MemoryScope.global_,
                kind=MemoryKind.explicit_user_rule,
                text=text,
                table_scope=resolve_table_scope(self._last_sql, known_tables),
            )
            # Immediate, durable persist — unlike corrections (buffered until
            # harvest review), a recorded decision is a deliberate analyst
            # action and must survive even if the session ends before the
            # next harvest-review pass.
            MemoryStore().append(memory)
            self.notify("\U0001f9ed Decision recorded", timeout=4)

        self.app.push_screen(RecordDecisionScreen(), _on_result)

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

        from labrat.widgets.chat_panel import ChatPanel
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
            # A manually-run query invalidates any chart from a prior agent
            # turn — without this, pinning right after would pair fresh rows
            # with a stale chart (t5-M1).
            self._last_chart = None
            # ...and any accumulated grounding provenance — the agent didn't
            # produce this SQL, so stamping its trust block onto hand-edited
            # rows would fabricate provenance (whole-branch F1).
            chat = self.query_one("#chat-content", ChatPanel)
            chat.last_turn_provenance = None
            table.load(df, execution_time=elapsed_ms)
            table.display = True
            self._record_edit_if_diverged(sql)
        except Exception as exc:
            chart_log.write(f"[bold red]SQL error:[/bold red] {exc}")
            chart_log.display = True
            table.display = False
        finally:
            loading.display = False

    # ── actions ──────────────────────────────────────────────────────────────

    # ── pin-time capture (Cheese v1 Task 5) ─────────────────────────────────

    def _capture_finding(self, *, question: str, sql: str, note: str = "") -> Finding | None:
        """Shared capture path for both the upgraded pin handler and f8 share.

        Snapshots the current results (+ chart, if any) into FindingDataStore,
        snapshots this turn's grounding provenance, and pins a Finding. Never
        raises into the TUI: a chart-render failure just omits the chart PNG.
        Returns None (after notifying) if the agent is still mid-turn — pinning
        then would snapshot a partial answer.
        """
        import labrat.cheese.store as cheese_store_mod
        from labrat.chart.render_image import render_image
        from labrat.cheese.store import FindingDataStore
        from labrat.maze.gitmeta import current_git_sha
        from labrat.maze.staleness import fingerprint_from_catalog
        from labrat.thread.findings import FindingsManager
        from labrat.widgets.chat_panel import ChatPanel
        from labrat.widgets.results_table import ResultsTable

        chat = self.query_one("#chat-content", ChatPanel)
        if chat.is_agent_busy:
            self.notify("Wait for the agent to finish before sharing.", severity="warning")
            return None

        table = self.query_one("#results-content", ResultsTable)
        df = table.source_df
        finding_id = str(uuid.uuid4())
        results_ref: str | None = None
        chart_spec_dump: dict[str, Any] | None = None
        try:
            if df.height > 0 or self._last_chart is not None:
                chart_png: bytes | None = None
                if self._last_chart is not None:
                    spec, chart_df = self._last_chart
                    chart_spec_dump = spec.model_dump(mode="json")
                    try:
                        chart_png = render_image(spec, chart_df)
                    except Exception:  # chart failure degrades to omission, never raises
                        chart_png = None
                data_store = FindingDataStore(cheese_store_mod.DEFAULT_DATA_ROOT)
                results_ref = data_store.capture(finding_id, df, chart_png=chart_png)

            prov = None
            if chat.last_turn_provenance is not None:
                schema_fingerprint = (
                    fingerprint_from_catalog(self._catalog) if self._catalog is not None else None
                )
                prov = chat.last_turn_provenance.snapshot(
                    schema_fingerprint=schema_fingerprint,
                    git_sha=current_git_sha(Path.cwd()),
                    model_id=getattr(self._profile_obj, "agent_model", None),
                )

            mgr = FindingsManager()
            return mgr.pin(
                finding_id=finding_id,
                version_id=self._current_thread_id or "unknown",
                question=question,
                sql=sql,
                results_ref=results_ref,
                chart_spec=chart_spec_dump,
                note=note,
                provenance=prov,
            )
        except Exception as exc:  # disk/permission errors must not raise into Textual
            self.notify(f"Couldn't save finding: {exc}", severity="error")
            return None

    # ── pin finding handler (M18) ─────────────────────────────────────────────

    def on_results_table_pin_requested(self) -> None:
        sql = self._last_sql
        if not sql:
            self.notify("Run a query first before pinning.", severity="warning")
            return
        finding = self._capture_finding(question=self._last_user_prompt or sql, sql=sql)
        if finding is None:
            return  # _capture_finding already notified (agent busy)
        self.notify("Finding pinned!  Press Ctrl+K to view findings.", timeout=3)

    # ── share answer (Cheese v1 Task 5, f8) ─────────────────────────────────

    def action_share_answer(self) -> None:
        if not self._last_sql:
            self.notify("Run a query first, then share.", severity="warning")
            return
        finding = self._capture_finding(
            question=self._last_user_prompt or self._last_sql, sql=self._last_sql
        )
        if finding is None:
            return  # _capture_finding already notified (agent busy)
        try:
            import labrat.cheese.store as cheese_store_mod
            from labrat.cheese.export import export_cheese
            from labrat.cheese.store import CheeseStore, FindingDataStore

            path = export_cheese(
                [finding],
                kind="single",
                title=finding.question,
                cheese_store=CheeseStore(cheese_store_mod.DEFAULT_CHEESE_ROOT),
                data_store=FindingDataStore(cheese_store_mod.DEFAULT_DATA_ROOT),
            )
        except Exception as exc:  # never raise into the TUI
            self.notify(f"Cheese export failed: {exc}", severity="error")
            return
        self.notify(f"\U0001f9c0 Cheese exported: {path}", timeout=6)

    # ── new modal actions ─────────────────────────────────────────────────────

    def action_manage_threads(self) -> None:
        from labrat.screens.thread_manager import ThreadManagerScreen

        def _on_result(thread_id: str | None) -> None:
            if not thread_id:
                return
            if (
                self._profile_obj is not None
                and self._profile_obj.harvest_opt_in
                and self._correction_buffer.pending_count > 0
            ):
                from labrat.screens.confirm import ConfirmScreen

                n = self._correction_buffer.pending_count

                def _maybe_harvest(confirmed: bool | None) -> None:
                    if confirmed:
                        self._run_harvest_review()

                self.app.push_screen(
                    ConfirmScreen(
                        f"[bold]{n} correction(s) captured this session.[/bold]\n\n"
                        "Review learnings before switching threads?"
                    ),
                    _maybe_harvest,
                )
            t = self._thread_manager.get_thread(thread_id)
            if t is None:
                return
            self._current_thread_id = t.id
            self._current_thread_name = t.name
            # Switching threads leaves the old thread's chart/prompt behind —
            # otherwise a stale chart could get paired with the new thread's
            # results at pin time (t5-M1). Also drop the old thread's
            # grounding provenance — it belongs to a turn from a different
            # thread and must never be stamped onto this thread's findings
            # (whole-branch F1).
            self._last_chart = None
            self._last_user_prompt = ""
            from labrat.widgets.chat_panel import ChatPanel

            self.query_one("#chat-content", ChatPanel).last_turn_provenance = None
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
        from labrat.profile.model import Profile
        from labrat.screens.findings_viewer import FindingsViewerScreen

        profile_obj = self._profile_obj or Profile(
            name=self._profile if self._profile != "—" else "default",
            dialect=self._dialect if self._dialect != "—" else "duckdb",
        )
        self.app.push_screen(FindingsViewerScreen(profile_obj=profile_obj, catalog=self._catalog))

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

    def action_open_settings(self) -> None:
        from labrat.screens.settings import SettingsScreen

        if self._profile_obj is None:
            self.notify("No profile loaded — settings unavailable.", severity="warning")
            return

        def _on_result(updated: object) -> None:
            from labrat.profile.model import Profile

            if isinstance(updated, Profile):
                self._profile_obj = updated
                self.notify("Settings saved. Provider/verify apply on next start.", timeout=4)

        self.app.push_screen(SettingsScreen(self._profile_obj), _on_result)

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
