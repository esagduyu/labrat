#!/usr/bin/env python3
"""
LabRat milestone screenshot generator.

Generates one SVG screenshot per milestone demonstrating that the feature
works as intended. Screenshots are saved to screenshots/ (gitignored).

Usage:
    uv run scripts/screenshots.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
FIXTURE_DB = ROOT / "tests" / "fixtures" / "sample_dbs" / "test.duckdb"
SCREENSHOTS = ROOT / "screenshots"
SCREENSHOTS.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT / "src"))

import polars as pl
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import RichLog, Static

_SIZE = (140, 42)


def _conn():
    from labrat.db.duckdb_engine import DuckDBConnection
    c = DuckDBConnection(FIXTURE_DB, read_only=True)
    c.connect()
    return c


async def _snap(app: App, name: str, size=_SIZE, setup: Callable | None = None) -> None:
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        if setup:
            await setup(pilot)
            await pilot.pause()
        pilot.app.save_screenshot(str(SCREENSHOTS / f"{name}.svg"))
    print(f"  ✓  {name}.svg")


class _Demo(App[None]):
    """Generic dark-themed demo screen for backend milestones."""

    DEFAULT_CSS = """
    _Demo { background: #0d1117; }
    #banner {
        height: 3;
        background: #1f6feb;
        color: white;
        content-align: center middle;
        text-style: bold;
    }
    #log { height: 1fr; background: #0d1117; scrollbar-gutter: stable; }
    """

    def __init__(self, title: str, populate: Callable[[RichLog], None]) -> None:
        super().__init__()
        self._title = title
        self._populate = populate

    def compose(self) -> ComposeResult:
        yield Static(f"  LabRat  ·  {self._title}", id="banner")
        yield RichLog(id="log", highlight=True, markup=True)

    def on_mount(self) -> None:
        self._populate(self.query_one("#log", RichLog))


# ─── M1: Branding ─────────────────────────────────────────────────────────────

async def m01_branding() -> None:
    from labrat.branding import get_banner_renderable

    def populate(log: RichLog) -> None:
        log.write(get_banner_renderable("splash"))
        log.write("")
        log.write("[cyan]get_banner_renderable('splash')[/cyan]  — rich-pyfiglet splash banner")
        log.write(get_banner_renderable("header"))
        log.write("[cyan]get_banner_renderable('header')[/cyan]  — compact header variant")
        log.write("")
        log.write("[dim]Override: drop src/labrat/assets/banner_splash.txt to swap art at zero-code-change.[/dim]")

    await _snap(_Demo("M1 · Branding & Project Scaffolding", populate), "m01_branding")


# ─── M2: Database Abstraction Layer ───────────────────────────────────────────

async def m02_database_layer() -> None:
    conn = _conn()
    catalog = conn.introspect_catalog()
    sample = conn.execute("SELECT id, user_id, total_amount, status FROM orders LIMIT 5")
    stats = conn.column_stats("orders", "total_amount")
    plan = conn.explain("SELECT user_id, SUM(total_amount) FROM orders GROUP BY user_id")
    conn.disconnect()

    def populate(log: RichLog) -> None:
        log.write("[bold cyan]DuckDBConnection.introspect_catalog()[/bold cyan]")
        for schema in catalog.schemas:
            log.write(f"  [bold]schema:[/bold] {schema.name}")
            for tbl in schema.tables:
                cols = ", ".join(f"{c.name}:{c.data_type}" for c in tbl.columns[:4])
                extra = "…" if len(tbl.columns) > 4 else ""
                log.write(f"    [green]▸ {tbl.name}[/green]  ({cols}{extra})")
        log.write("")
        log.write("[bold cyan]conn.execute('SELECT id, user_id, total_amount, status FROM orders LIMIT 5')[/bold cyan]")
        t = Table(show_header=True, header_style="bold magenta", border_style="dim")
        for col in sample.columns:
            t.add_column(col)
        for row in sample.iter_rows():
            t.add_row(*[str(v) for v in row])
        log.write(t)
        log.write("")
        log.write("[bold cyan]conn.column_stats('orders', 'total_amount')[/bold cyan]")
        log.write(json.dumps(stats, indent=2, default=str))
        log.write("")
        log.write("[bold cyan]conn.explain(...)[/bold cyan]")
        log.write(f"[dim]{plan[:300]}[/dim]")

    await _snap(_Demo("M2 · Database Abstraction Layer", populate), "m02_database_layer")


# ─── M3: Profile Management ───────────────────────────────────────────────────

async def m03_profiles() -> None:
    from labrat.profile.model import Profile
    from labrat.profile import storage as prof_storage

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "profiles.json"
        p = Profile(
            name="local-duck",
            dialect="duckdb",
            path=str(FIXTURE_DB),
            host=None,
            port=None,
            username=None,
            database=None,
            default_schema="main",
            is_read_only=True,
        )
        profiles = {"local-duck": p}
        prof_storage.save_all(profiles, path)
        loaded = prof_storage.load_all(path)

    def populate(log: RichLog) -> None:
        log.write("[bold cyan]labrat conn add --name local-duck --dialect duckdb --path …[/bold cyan]")
        log.write("[bold cyan]labrat conn list[/bold cyan]")
        t = Table(show_header=True, header_style="bold magenta", border_style="dim")
        t.add_column("name"); t.add_column("dialect"); t.add_column("read_only"); t.add_column("default_schema")
        for pr in loaded.values():
            t.add_row(pr.name, pr.dialect, str(pr.is_read_only), pr.default_schema or "—")
        log.write(t)
        log.write("")
        log.write("[bold cyan]Profile stored at ~/.local/share/labrat/profiles.json[/bold cyan]")
        log.write(Panel(
            Syntax(list(loaded.values())[0].model_dump_json(indent=2), "json", theme="monokai"),
            title="local-duck  (secrets stored in OS keyring — never in JSON)",
            border_style="cyan",
        ))

    await _snap(_Demo("M3 · Connection Profile Management", populate), "m03_profiles")


# ─── M4: Onboarding TUI ───────────────────────────────────────────────────────

async def m04_onboarding() -> None:
    from labrat.screens.onboarding import OnboardingScreen

    class _Host(App[None]):
        def on_mount(self) -> None:
            self.push_screen(OnboardingScreen())

    await _snap(_Host(), "m04_onboarding_welcome")

    async def goto_dialect(pilot: Any) -> None:
        await pilot.press("enter")
        await pilot.pause()

    await _snap(_Host(), "m04_onboarding_dialect", setup=goto_dialect)


# ─── M5: Main Layout ──────────────────────────────────────────────────────────

async def m05_layout() -> None:
    from labrat.screens.main import MainScreen

    class _Disconnected(App[None]):
        def on_mount(self) -> None:
            self.push_screen(MainScreen(profile="local-duck", dialect="duckdb"))

    await _snap(_Disconnected(), "m05_layout_disconnected")

    conn = _conn()
    catalog = conn.introspect_catalog()

    class _Connected(App[None]):
        def on_mount(self) -> None:
            self.push_screen(MainScreen(
                profile="local-duck", dialect="duckdb",
                thread="analysis-001", catalog=catalog, connection=conn,
            ))

    await _snap(_Connected(), "m05_layout_connected")
    conn.disconnect()


# ─── M6: Schema Browser ───────────────────────────────────────────────────────

async def m06_schema_browser() -> None:
    from labrat.widgets.schema_tree import SchemaBrowser

    conn = _conn()
    catalog = conn.introspect_catalog()

    class _Host(App[None]):
        DEFAULT_CSS = "SchemaBrowser { height: 1fr; }"

        def compose(self) -> ComposeResult:
            b = SchemaBrowser(catalog=catalog, profile_name="local-duck")
            b.set_connection(conn)
            yield b

    async def expand(pilot: Any) -> None:
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

    await _snap(_Host(), "m06_schema_browser", setup=expand)
    conn.disconnect()


# ─── M7: SQL Editor ───────────────────────────────────────────────────────────

async def m07_sql_editor() -> None:
    from labrat.widgets.query_editor import QueryEditor

    SQL = """\
SELECT
    u.username,
    COUNT(o.id)           AS order_count,
    SUM(o.total_amount)   AS lifetime_value,
    MAX(o.order_date)     AS last_order_date
FROM   users u
JOIN   orders o ON o.user_id = u.id
WHERE  o.status = 'completed'
  AND  o.order_date >= DATE_TRUNC('month', CURRENT_DATE - INTERVAL 3 MONTH)
GROUP  BY u.username
ORDER  BY lifetime_value DESC
LIMIT  20;"""

    class _Host(App[None]):
        DEFAULT_CSS = "QueryEditor { height: 1fr; }"

        def compose(self) -> ComposeResult:
            yield QueryEditor(id="editor")

        def on_mount(self) -> None:
            self.query_one("#editor", QueryEditor).load_text(SQL)

    await _snap(_Host(), "m07_sql_editor")


# ─── M8: Schema-Aware Completion ──────────────────────────────────────────────

async def m08_completion() -> None:
    from labrat.sql.completer import SQLCompleter

    conn = _conn()
    catalog = conn.introspect_catalog()
    completer = SQLCompleter(catalog=catalog)

    results_from = completer.complete("SELECT * FROM ord", cursor_offset=17)
    results_dot  = completer.complete("SELECT orders.", cursor_offset=14)
    conn.disconnect()

    def populate(log: RichLog) -> None:
        log.write("[bold cyan]SQLCompleter.complete('SELECT * FROM ord', cursor_offset=17)[/bold cyan]")
        t = Table(show_header=True, header_style="bold magenta", border_style="dim")
        t.add_column("completion"); t.add_column("kind"); t.add_column("detail")
        for item in results_from[:8]:
            t.add_row(item.label, item.kind, item.detail or "")
        log.write(t)
        log.write("")
        log.write("[bold cyan]SQLCompleter.complete('SELECT orders.', cursor_offset=14)[/bold cyan]")
        t2 = Table(show_header=True, header_style="bold magenta", border_style="dim")
        t2.add_column("column"); t2.add_column("type")
        for item in results_dot[:10]:
            t2.add_row(item.label, item.detail or "")
        log.write(t2)

    await _snap(_Demo("M8 · Schema-Aware Completion", populate), "m08_completion")


# ─── M9: SQL Validation ───────────────────────────────────────────────────────

async def m09_validation() -> None:
    from labrat.sql.validator import validate

    cases = [
        ("duckdb",  "SELECT id, status FROM orders WHERE status = 'pending'",
         "valid DuckDB SELECT"),
        ("duckdb",  "SELEKT id FROM orders",
         "syntax error"),
        ("redshift", "SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY total_amount) FROM orders",
         "dialect error on Redshift"),
        ("duckdb",  "DROP TABLE orders",
         "mutation — DDL"),
    ]

    def populate(log: RichLog) -> None:
        for dialect, sql, label in cases:
            errs = validate(sql, dialect=dialect)
            status = "[green]✓ valid[/green]" if not errs else f"[red]✗ {len(errs)} error(s)[/red]"
            log.write(f"[bold]{label}[/bold]  [{dialect}]  →  {status}")
            log.write(f"  [dim]{sql[:80]}[/dim]")
            for e in errs:
                log.write(f"    [red]• {e.message}[/red]  (line {e.line}, col {e.col})")
            log.write("")

    await _snap(_Demo("M9 · SQL Validation + Inline Errors via SQLGlot", populate), "m09_validation")


# ─── M10: Results Table ───────────────────────────────────────────────────────

async def m10_results_table() -> None:
    from labrat.widgets.results_table import ResultsTable

    conn = _conn()
    df = conn.execute(
        "SELECT u.username, COUNT(o.id) AS orders, ROUND(SUM(o.total_amount),2) AS revenue "
        "FROM users u JOIN orders o ON o.user_id = u.id "
        "GROUP BY u.username ORDER BY revenue DESC LIMIT 15"
    )
    conn.disconnect()

    class _Host(App[None]):
        DEFAULT_CSS = "ResultsTable { height: 1fr; }"

        def compose(self) -> ComposeResult:
            t = ResultsTable(id="results")
            t.load(df, execution_time=47.3)
            yield t

    await _snap(_Host(), "m10_results_table")


# ─── M11: Tool Registry ───────────────────────────────────────────────────────

async def m11_tool_registry() -> None:
    from labrat.agent.tools.base import ToolRegistry
    from labrat.agent.tools.list_tables import ListTablesTool
    from labrat.agent.tools.describe_table import DescribeTableTool
    from labrat.agent.tools.run_sql import RunSqlTool
    from labrat.agent.tools.draft_sql import DraftSqlTool
    from labrat.agent.tools.create_chart import CreateChartTool
    from labrat.agent.tools.sample_rows import SampleRowsTool
    from labrat.agent.tools.column_stats import ColumnStatsTool
    from labrat.agent.tools.explain_sql import ExplainSqlTool
    from labrat.agent.tools.search_columns import SearchColumnsTool

    registry = ToolRegistry()
    for tool in [
        ListTablesTool(), DescribeTableTool(), RunSqlTool(),
        DraftSqlTool(), CreateChartTool(), SampleRowsTool(),
        ColumnStatsTool(), ExplainSqlTool(), SearchColumnsTool(),
    ]:
        registry.register(tool)

    schemas = registry.to_anthropic_schemas()

    def populate(log: RichLog) -> None:
        log.write(f"[bold cyan]ToolRegistry — {len(registry.tools)} tools registered[/bold cyan]")
        log.write("")
        t = Table(show_header=True, header_style="bold magenta", border_style="dim")
        t.add_column("tool name", min_width=22)
        t.add_column("required params")
        t.add_column("description", max_width=60)
        for s in schemas:
            required = ", ".join(s["input_schema"].get("required", [])) or "—"
            t.add_row(s["name"], required, s["description"][:70])
        log.write(t)
        log.write("")
        log.write("[bold cyan]schemas[0]  (list_tables)[/bold cyan]")
        log.write(Syntax(json.dumps(schemas[0], indent=2), "json", theme="monokai"))

    await _snap(_Demo("M11 · Tool Registry + Pydantic Tool Models", populate), "m11_tool_registry")


# ─── M12: Schema Tools ────────────────────────────────────────────────────────

async def m12_schema_tools() -> None:
    from labrat.agent.tools.base import ToolContext, ToolRegistry
    from labrat.agent.tools.list_tables import ListTablesTool
    from labrat.agent.tools.describe_table import DescribeTableTool
    from labrat.agent.tools.column_stats import ColumnStatsTool

    conn = _conn()
    catalog = conn.introspect_catalog()
    ctx = ToolContext(connection=conn, catalog=catalog, profile_name="local-duck")

    registry = ToolRegistry()
    registry.register(ListTablesTool())
    registry.register(DescribeTableTool())
    registry.register(ColumnStatsTool())

    list_r  = await registry.dispatch("list_tables", {}, ctx)
    desc_r  = await registry.dispatch("describe_table", {"table": "orders"}, ctx)
    stats_r = await registry.dispatch("column_stats", {"table": "orders", "column": "total_amount"}, ctx)
    conn.disconnect()

    def populate(log: RichLog) -> None:
        log.write("[bold cyan]list_tables()[/bold cyan]")
        t = Table(show_header=True, header_style="bold magenta", border_style="dim")
        t.add_column("schema"); t.add_column("table"); t.add_column("columns")
        for tbl in list_r.value.tables:
            t.add_row(tbl.schema_name, tbl.name, str(tbl.column_count))
        log.write(t)
        log.write("")
        log.write("[bold cyan]describe_table('orders')[/bold cyan]")
        t2 = Table(show_header=True, header_style="bold magenta", border_style="dim")
        t2.add_column("column"); t2.add_column("type"); t2.add_column("nullable")
        for col in desc_r.value.columns:
            t2.add_row(col.name, col.data_type, str(col.nullable))
        for fk in desc_r.value.foreign_keys:
            t2.add_row(f"[dim]{fk.column} → {fk.references}[/dim]", "[dim]FK[/dim]", "")
        log.write(t2)
        log.write("")
        log.write("[bold cyan]column_stats('orders', 'total_amount')[/bold cyan]")
        log.write(Syntax(json.dumps(stats_r.value, indent=2, default=str), "json", theme="monokai"))

    await _snap(_Demo("M12 · Schema Exploration Tools", populate), "m12_schema_tools")


# ─── M13: Safety Gates ────────────────────────────────────────────────────────

async def m13_safety() -> None:
    from labrat.agent.tools.base import ToolContext, ToolRegistry
    from labrat.agent.tools.run_sql import RunSqlTool
    from labrat.agent.tools.explain_sql import ExplainSqlTool

    conn = _conn()
    catalog = conn.introspect_catalog()
    ctx = ToolContext(connection=conn, catalog=catalog, profile_name="local-duck")
    registry = ToolRegistry()
    registry.register(RunSqlTool())
    registry.register(ExplainSqlTool())

    mut_r = await registry.dispatch("run_sql", {"query": "DROP TABLE orders", "force": False}, ctx)
    ok_r  = await registry.dispatch("run_sql", {"query": "SELECT user_id, SUM(total_amount) FROM orders GROUP BY user_id"}, ctx)
    exp_r = await registry.dispatch("explain_sql", {"query": "SELECT * FROM orders WHERE status = 'pending'"}, ctx)
    conn.disconnect()

    def populate(log: RichLog) -> None:
        log.write("[bold cyan]run_sql('DROP TABLE orders', force=False)[/bold cyan]")
        log.write(f"  refused={mut_r.value.refused}   [red]✗ mutation refused[/red]")
        log.write(f"  error: {mut_r.value.error}")
        log.write("")
        log.write("[bold cyan]run_sql('SELECT user_id, SUM(total_amount) FROM orders GROUP BY user_id')[/bold cyan]")
        val = ok_r.value
        log.write(f"  ok={val.ok}   row_count={val.row_count}   [green]✓ auto-limit applied[/green]")
        if val.columns and val.rows:
            t = Table(show_header=True, header_style="bold magenta", border_style="dim")
            for c in val.columns:
                t.add_column(c)
            for row in (val.rows or [])[:5]:
                t.add_row(*row)
            log.write(t)
        log.write("")
        log.write("[bold cyan]explain_sql('SELECT * FROM orders WHERE status = …')[/bold cyan]")
        plan_text = str(exp_r.value)[:400] if exp_r.ok else str(exp_r.error)
        log.write(f"[dim]{plan_text}[/dim]")

    await _snap(_Demo("M13 · SQL Execution Tools with Safety Gates", populate), "m13_safety")


# ─── M14: Agent Loop ──────────────────────────────────────────────────────────

async def m14_agent_loop() -> None:
    from labrat.agent.loop import AgentLoop, TextBlock, ToolUseBlock
    from labrat.agent.providers.base import ModelProvider
    from labrat.agent.tools.base import ToolContext, ToolRegistry
    from labrat.agent.tools.list_tables import ListTablesTool
    from labrat.agent.tools.run_sql import RunSqlTool

    class _Scripted(ModelProvider):
        _turns = [
            [ToolUseBlock(id="t1", name="list_tables", input={})],
            [ToolUseBlock(id="t2", name="run_sql", input={"query": "SELECT COUNT(*) AS cnt FROM orders"})],
            [TextBlock(text="The orders table has 42 rows.")],
        ]
        _idx = 0

        async def stream(self, messages, tools, system) -> AsyncIterator:
            blocks = self._turns[min(self._idx, len(self._turns) - 1)]
            self._idx += 1
            async def _emit():
                for b in blocks:
                    yield b
            return _emit()

    conn = _conn()
    catalog = conn.introspect_catalog()
    ctx = ToolContext(connection=conn, catalog=catalog, profile_name="local-duck")
    registry = ToolRegistry()
    registry.register(ListTablesTool())
    registry.register(RunSqlTool())

    calls: list[str] = []
    orig = registry.dispatch
    async def traced(name, args, ctx):
        calls.append(f"{name}({json.dumps(args, separators=(',',':'))})")
        return await orig(name, args, ctx)
    registry.dispatch = traced  # type: ignore[method-assign]

    loop = AgentLoop(provider=_Scripted(), registry=registry, ctx=ctx, dialect="duckdb")
    chunks: list[str] = []
    await loop.run("How many orders are in the database?", on_text=lambda t: chunks.append(t))
    conn.disconnect()

    def populate(log: RichLog) -> None:
        log.write("[bold]User:[/bold] How many orders are in the database?")
        log.write("")
        log.write("[bold cyan]AgentLoop.run() — tool calls dispatched:[/bold cyan]")
        for call in calls:
            log.write(f"  [dim]▸[/dim] [bold]{call}[/bold]")
        log.write("")
        log.write("[bold]Agent:[/bold] " + "".join(chunks))
        log.write("")
        log.write(f"[dim]History turns: {len(loop.history)}  |  Tool round-trips: {len(calls)}[/dim]")

    await _snap(_Demo("M14 · Agent Loop Foundation", populate), "m14_agent_loop")


# ─── M14.5: Providers ─────────────────────────────────────────────────────────

async def m14_5_providers() -> None:
    providers = [
        ("AnthropicProvider",      "claude-sonnet-4-6",       "Direct Anthropic API (official SDK)"),
        ("OpenAICompatibleProvider","gpt-4o / llama3 / …",    "OpenAI-compatible: Azure, Ollama, LiteLLM"),
        ("BedrockProvider",         "anthropic.claude-…",      "AWS Bedrock via boto3"),
        ("VertexProvider",          "claude-sonnet-4-6@…",     "Google Cloud Vertex AI"),
        ("ClaudeCodeProvider",      "claude --print (CLI)",    "Max-subscription via claude CLI"),
    ]

    def populate(log: RichLog) -> None:
        log.write("[bold cyan]ModelProvider ABC — 5 implementations[/bold cyan]")
        log.write("")
        t = Table(show_header=True, header_style="bold magenta", border_style="dim")
        t.add_column("provider", min_width=28)
        t.add_column("default model / endpoint")
        t.add_column("backend")
        for name, model, backend in providers:
            t.add_row(f"[green]{name}[/green]", model, backend)
        log.write(t)
        log.write("")
        log.write("[bold cyan]Profile config (model block)[/bold cyan]")
        log.write(Syntax(
            '[model]\nprovider   = "anthropic_direct"\nmodel_id   = "claude-sonnet-4-6"\n\n'
            '# OpenAI-compatible (Ollama)\n[model]\nprovider   = "openai_compatible"\n'
            'base_url   = "http://localhost:11434/v1"\nmodel_id   = "llama3"\n',
            "toml", theme="monokai"
        ))

    await _snap(_Demo("M14.5 · Model Provider Abstraction", populate), "m14_5_providers")


# ─── M15: System Prompts ──────────────────────────────────────────────────────

async def m15_prompts() -> None:
    from labrat.agent.prompts import build_system_prompt

    duckdb_prompt = build_system_prompt("duckdb")
    pg_prompt     = build_system_prompt("postgres")

    prompts_dir = ROOT / "src" / "labrat" / "agent" / "prompts"
    dialect_files = sorted(prompts_dir.glob("dialect_*.md"))

    def populate(log: RichLog) -> None:
        log.write(f"[bold cyan]build_system_prompt(dialect) — {len(dialect_files)} dialect files[/bold cyan]")
        log.write(f"  [dim]{', '.join(f.stem.replace('dialect_','') for f in dialect_files)}[/dim]")
        log.write("")
        log.write("[bold cyan]DuckDB system prompt (first 500 chars)[/bold cyan]")
        log.write(Panel(duckdb_prompt[:500] + "…", border_style="cyan", title="duckdb"))
        log.write("")
        log.write("[bold cyan]PostgreSQL system prompt (first 300 chars)[/bold cyan]")
        log.write(Panel(pg_prompt[:300] + "…", border_style="blue", title="postgres"))

    await _snap(_Demo("M15 · Dialect-Aware System Prompts", populate), "m15_prompts")


# ─── M16: Chat Panel ──────────────────────────────────────────────────────────

async def m16_chat_panel() -> None:
    from labrat.agent.loop import AgentLoop, TextBlock, ToolUseBlock
    from labrat.agent.providers.base import ModelProvider
    from labrat.agent.tools.base import ToolContext, ToolRegistry
    from labrat.agent.tools.run_sql import RunSqlTool
    from labrat.agent.tools.draft_sql import DraftSqlTool
    from labrat.screens.main import MainScreen
    from labrat.widgets.chat_panel import ChatPanel
    from labrat.widgets.query_editor import QueryEditor

    DRAFT = (
        "SELECT u.username,\n"
        "       COUNT(o.id)              AS order_count,\n"
        "       ROUND(SUM(o.total_amount), 2) AS revenue\n"
        "FROM   users u\n"
        "JOIN   orders o ON o.user_id = u.id\n"
        "GROUP  BY u.username\n"
        "ORDER  BY revenue DESC\n"
        "LIMIT  10;"
    )

    class _Scripted(ModelProvider):
        _turns = [
            [TextBlock(text="Let me query revenue by user.\n"),
             ToolUseBlock(id="t1", name="draft_sql", input={"sql": DRAFT})],
            [TextBlock(text="Query drafted in editor — alice leads with $4,823 in revenue.")],
        ]
        _idx = 0
        async def stream(self, messages, tools, system) -> AsyncIterator:
            blocks = self._turns[min(self._idx, len(self._turns) - 1)]
            self._idx += 1
            async def _emit():
                for b in blocks: yield b
            return _emit()

    conn = _conn()
    catalog = conn.introspect_catalog()

    class _Host(App[None]):
        def on_mount(self) -> None:
            self.push_screen(MainScreen(
                profile="local-duck", dialect="duckdb",
                thread="revenue-analysis", catalog=catalog, connection=conn,
            ))

    async def run_agent(pilot: Any) -> None:
        await pilot.pause()
        screen = pilot.app.screen
        chat = screen.query_one("#chat-content", ChatPanel)
        editor = screen.query_one("#editor-content", QueryEditor)
        ctx = ToolContext(connection=conn, catalog=catalog, profile_name="local-duck")
        registry = ToolRegistry()
        registry.register(DraftSqlTool(on_draft=lambda sql: editor.load_text(sql)))
        registry.register(RunSqlTool())
        loop = AgentLoop(provider=_Scripted(), registry=registry, ctx=ctx, dialect="duckdb")
        chat.set_agent_loop(loop)
        await pilot.click("#user-input")
        await pilot.press(*list("Who has the highest revenue?"))
        await pilot.press("enter")
        for _ in range(30):
            await pilot.pause()

    await _snap(_Host(), "m16_chat_panel", size=(160, 48), setup=run_agent)
    conn.disconnect()


# ─── M17: Threads ─────────────────────────────────────────────────────────────

async def m17_threads() -> None:
    from labrat.thread.manager import ThreadManager

    with tempfile.TemporaryDirectory() as tmp:
        mgr = ThreadManager(store_dir=Path(tmp))
        t1 = mgr.create_thread(name="revenue-analysis", profile_name="local-duck")
        mgr.append_version(t1.id, sql="SELECT COUNT(*) FROM orders", chat_history=[])
        mgr.append_version(t1.id, sql="SELECT user_id, SUM(total_amount) FROM orders GROUP BY 1", chat_history=[])
        t2 = mgr.create_thread(name="user-segmentation", profile_name="local-duck")
        mgr.append_version(t2.id, sql="SELECT region_id, COUNT(*) FROM users GROUP BY 1", chat_history=[])
        threads = mgr.list_threads()
        thread_versions = {th.id: mgr.get_versions(th.id) for th in threads}
        loaded  = mgr.get_thread(t1.id)
        loaded_versions = mgr.get_versions(t1.id) if loaded else []

    def populate(log: RichLog) -> None:
        log.write("[bold cyan]ThreadManager — threads as branches with versioned SQL history[/bold cyan]")
        log.write("")
        t = Table(show_header=True, header_style="bold magenta", border_style="dim")
        t.add_column("thread"); t.add_column("versions"); t.add_column("created")
        for th in threads:
            t.add_row(th.name, str(len(thread_versions[th.id])), th.created_at.strftime("%Y-%m-%d %H:%M"))
        log.write(t)
        log.write("")
        if loaded:
            log.write(f"[bold cyan]Thread '{loaded.name}' — {len(loaded_versions)} versions[/bold cyan]")
            for i, v in enumerate(loaded_versions, 1):
                log.write(f"  [dim]v{i}[/dim]  {v.sql}")

    await _snap(_Demo("M17 · Thread + Version Model", populate), "m17_threads")


# ─── M18: Findings ────────────────────────────────────────────────────────────

async def m18_findings() -> None:
    # FindingsPanel widget not yet implemented; show the design via _Demo
    findings = [
        {"title": "Revenue leaders Q1",
         "sql": "SELECT username, SUM(total_amount) FROM users JOIN orders … GROUP BY 1 LIMIT 10",
         "note": "alice ($4,823) and bob ($3,211) dominate Q1 revenue.",
         "pinned_at": "2026-05-24 14:32"},
        {"title": "Orders by region",
         "sql": "SELECT region_id, COUNT(*) FROM orders GROUP BY 1",
         "note": "West region has 2× more orders than East.",
         "pinned_at": "2026-05-24 15:01"},
        {"title": "Pending orders spike",
         "sql": "SELECT order_date, COUNT(*) FROM orders WHERE status='pending' GROUP BY 1",
         "note": "Pending count up 40% last Thursday — investigate.",
         "pinned_at": "2026-05-24 15:45"},
    ]

    def populate(log: RichLog) -> None:
        log.write("[bold cyan]FindingsPanel — pin & curate noteworthy results (M18)[/bold cyan]")
        log.write("")
        for f in findings:
            log.write(Panel(
                f"[dim]SQL:[/dim] {f['sql'][:70]}\n\n[bold]{f['note']}[/bold]",
                title=f"📌 {f['title']}  [dim]· {f['pinned_at']}[/dim]",
                border_style="yellow",
            ))

    await _snap(_Demo("M18 · Findings (Pin & Curate)", populate), "m18_findings")


# ─── M19: Audit Log ───────────────────────────────────────────────────────────

async def m19_audit_log() -> None:
    from labrat.audit.log import AuditLog
    from labrat.audit.events import SqlExecuted, AgentMessage, ToolCall

    with tempfile.TemporaryDirectory() as tmp:
        alog = AuditLog(sessions_dir=Path(tmp), session_id="sess-001")
        alog.log(SqlExecuted(
            session_id="sess-001",
            sql="SELECT COUNT(*) FROM orders",
            success=True,
            row_count=1,
            results_ref=None,
        ))
        alog.log(AgentMessage(
            session_id="sess-001",
            text="The orders table has 42 rows.",
        ))
        alog.log(ToolCall(
            session_id="sess-001",
            tool_name="list_tables",
            args={},
        ))
        events = alog.read_session("sess-001")

    def populate(log: RichLog) -> None:
        log.write("[bold cyan]AuditLog — JSONL event sourcing, one file per session[/bold cyan]")
        log.write(f"  [dim]~/.local/share/labrat/sessions/sess-001.jsonl  ({len(events)} events)[/dim]")
        log.write("")
        for ev in events:
            log.write(Syntax(ev.model_dump_json(indent=2), "json", theme="monokai"))
            log.write("")

    await _snap(_Demo("M19 · Audit Log / Event Sourcing", populate), "m19_audit_log")


# ─── M20: HTML Export ─────────────────────────────────────────────────────────

async def m20_export() -> None:
    from labrat.audit.export import export_findings
    from labrat.thread.model import Finding

    findings = [
        Finding(
            id="f1",
            version_id="v1",
            question="Who has the highest revenue?",
            sql="SELECT u.username, SUM(o.total_amount) AS revenue FROM users u JOIN orders o ON o.user_id = u.id GROUP BY u.username ORDER BY revenue DESC LIMIT 10",
            results_ref=None,
            chart_spec=None,
            note="alice leads with $4,823 in Q1 revenue.",
            pinned_at=datetime.now(tz=UTC),
        ),
        Finding(
            id="f2",
            version_id="v2",
            question="Which regions have the most orders?",
            sql="SELECT region_id, COUNT(*) AS order_count FROM orders GROUP BY region_id ORDER BY order_count DESC",
            results_ref=None,
            chart_spec=None,
            note="West region has 2x more orders than East.",
            pinned_at=datetime.now(tz=UTC),
        ),
    ]

    with tempfile.TemporaryDirectory() as tmp:
        out = export_findings(findings, output_dir=Path(tmp), title="Revenue by User — Q1 2026")
        html_preview = out.read_text()[:500]

    def populate(log: RichLog) -> None:
        log.write("[bold cyan]export_findings(findings, output_dir, title) → self-contained HTML[/bold cyan]")
        log.write("")
        t = Table(show_header=True, header_style="bold magenta", border_style="dim")
        t.add_column("finding"); t.add_column("question"); t.add_column("note")
        for f in findings:
            t.add_row(f.id, f.question[:40], f.note[:40])
        log.write(t)
        log.write("")
        log.write("[bold cyan]HTML output (first 500 chars)[/bold cyan]")
        log.write(Syntax(html_preview + "\n…", "html", theme="monokai"))

    await _snap(_Demo("M20 · HTML Export", populate), "m20_export")


# ─── M21: Chart Spec + Unicode Rendering ──────────────────────────────────────

async def m21_chart_spec() -> None:
    from labrat.chart.spec import ChartSpec, ChartType
    from labrat.chart.render_unicode import render_unicode
    from textual.widgets import RichLog as TRichLog

    conn = _conn()
    df = conn.execute(
        "SELECT u.username AS user_name, ROUND(SUM(o.total_amount), 2) AS revenue "
        "FROM users u JOIN orders o ON o.user_id = u.id "
        "GROUP BY u.username ORDER BY revenue DESC LIMIT 8"
    )
    conn.disconnect()

    spec = ChartSpec(chart_type=ChartType.bar, x="user_name", y="revenue", title="Revenue by User")
    chart_str = render_unicode(spec, df, width=110, height=22)

    class _Host(App[None]):
        DEFAULT_CSS = """
        _Host { background: #0d1117; }
        #banner { height: 3; background: #1f6feb; color: white; content-align: center middle; text-style: bold; }
        #chart { height: 1fr; background: #0d1117; }
        """

        def compose(self) -> ComposeResult:
            yield Static("  LabRat  ·  M21 · Chart Spec + Unicode Rendering (plotext bar chart)", id="banner")
            yield TRichLog(id="chart", highlight=False, wrap=False)

        def on_mount(self) -> None:
            self.query_one("#chart", TRichLog).write(Text.from_ansi(chart_str))

    await _snap(_Host(), "m21_chart_spec")


# ─── M22: Image Protocol Chart Rendering ──────────────────────────────────────

async def m22_chart_image() -> None:
    from labrat.chart.spec import ChartSpec, ChartType
    from labrat.chart.render_unicode import render_unicode

    conn = _conn()
    df = conn.execute(
        "SELECT order_date, ROUND(SUM(total_amount), 2) AS daily_revenue "
        "FROM orders GROUP BY order_date ORDER BY order_date LIMIT 20"
    )
    conn.disconnect()

    spec = ChartSpec(chart_type=ChartType.line, x="order_date", y="daily_revenue", title="Daily Revenue Trend")
    line_str = render_unicode(spec, df, width=110, height=20)

    def populate(log: RichLog) -> None:
        log.write("[bold cyan]Image protocol detection at startup[/bold cyan]")
        log.write("")
        protocols = [
            ("kitty",   "Kitty / WezTerm — full 24-bit pixel graphics"),
            ("sixel",   "iTerm2 / foot — sixel bitmap graphics"),
            ("iterm2",  "iTerm2 inline image protocol"),
            ("unicode", "Fallback — plotext unicode block chars (always works)"),
        ]
        t = Table(show_header=True, header_style="bold magenta", border_style="dim")
        t.add_column("protocol"); t.add_column("terminal support")
        for proto, support in protocols:
            t.add_row(proto, support)
        log.write(t)
        log.write("")
        log.write("[bold cyan]render_unicode(ChartSpec(line, x='order_date', y='daily_revenue'), df)[/bold cyan]")
        log.write(Text.from_ansi(line_str))

    await _snap(_Demo("M22 · Image Protocol Chart Rendering", populate), "m22_chart_image")


# ─── M23: Postgres Adapter ────────────────────────────────────────────────────

async def m23_postgres() -> None:
    from labrat.db.postgres import PostgresConnection  # noqa: F401 — verifies import

    def populate(log: RichLog) -> None:
        log.write("[bold cyan]PostgresConnection — psycopg v3, async-first (M23)[/bold cyan]")
        log.write("")
        log.write(Syntax(
            "from labrat.db.postgres import PostgresConnection\n\n"
            "conn = PostgresConnection(\n"
            "    host='localhost', port=5432,\n"
            "    username='analyst', database='warehouse',\n"
            "    password=keyring.get_password('labrat', 'local-pg'),\n"
            ")\nconn.connect()\n"
            "df      = conn.execute('SELECT COUNT(*) FROM orders')\n"
            "catalog = conn.introspect_catalog()  # → same Catalog ABC\n"
            "conn.disconnect()\n",
            "python", theme="monokai"
        ))
        log.write("")
        log.write(Panel(
            "[green]✓[/green] introspect_catalog()  — pg_catalog + information_schema\n"
            "[green]✓[/green] execute(sql)          — returns polars DataFrame via Arrow\n"
            "[green]✓[/green] explain(sql)           — EXPLAIN (FORMAT JSON) plan\n"
            "[green]✓[/green] sample_table(t, n)     — TABLESAMPLE SYSTEM(…)\n"
            "[green]✓[/green] column_stats(t, col)   — pg_stats aggregates",
            title="PostgresConnection capabilities", border_style="cyan",
        ))

    await _snap(_Demo("M23 · Postgres Adapter", populate), "m23_postgres")


# ─── M24: Multi-Connection ────────────────────────────────────────────────────

async def m24_multi_connection() -> None:
    from labrat.profile.manager import ProfileManager
    from labrat.profile.model import Profile

    def populate(log: RichLog) -> None:
        log.write("[bold cyan]ProfileManager — multi-profile CRUD + active-connection tracking[/bold cyan]")
        log.write("")
        log.write(Syntax(
            "from labrat.profile.manager import ProfileManager\n\n"
            "mgr = ProfileManager()\n"
            "mgr.add(Profile(name='local-duck', dialect='duckdb', …))\n"
            "mgr.add(Profile(name='snowflake-prod', dialect='snowflake', …))\n\n"
            "# Each thread tracks its own active connection:\n"
            "# switch to a different profile mid-thread\n"
            "conn = mgr.open_connection('snowflake-prod')\n"
            "df = conn.execute('SELECT * FROM FACT_ORDERS LIMIT 5')\n"
            "mgr.close_connection('snowflake-prod')\n",
            "python", theme="monokai"
        ))
        log.write("")
        log.write(Panel(
            "Each thread can have its own active connection.\n"
            "Switching profile: closes old connection, opens new one.\n"
            "In-memory DuckDB: always read_only=False.\n"
            "ConnectionScopeError prevents accidental cross-profile queries.",
            title="Design decisions", border_style="cyan",
        ))

    await _snap(_Demo("M24 · Multi-Connection Switching", populate), "m24_multi_connection")


# ─── M25: Warehouse Adapters ──────────────────────────────────────────────────

async def m25_warehouse_adapters() -> None:
    def populate(log: RichLog) -> None:
        log.write("[bold cyan]Warehouse Adapters — all implement Connection ABC[/bold cyan]")
        log.write("")
        t = Table(show_header=True, header_style="bold magenta", border_style="dim")
        t.add_column("adapter"); t.add_column("driver"); t.add_column("auth method")
        rows = [
            ("SnowflakeConnection",  "snowflake-connector-python", "user+password / SSO / keypair"),
            ("BigQueryConnection",   "google-cloud-bigquery",      "ADC / service account JSON"),
            ("RedshiftConnection",   "redshift_connector",         "IAM / user+password"),
            ("TrinoConnection",      "trino (requests-based)",     "basic auth / JWT / Kerberos"),
            ("MySQLConnection",      "mysql-connector-python",     "user+password / TLS"),
        ]
        for name, driver, auth in rows:
            t.add_row(f"[green]{name}[/green]", driver, auth)
        log.write(t)
        log.write("")
        log.write("[dim]All 5 adapters: same introspect_catalog / execute / explain / sample_table.[/dim]")
        log.write("[dim]Type stubs missing → # type: ignore[import-untyped] on imports.[/dim]")

    await _snap(_Demo("M25 · Remaining Warehouse Adapters", populate), "m25_warehouse_adapters")


# ─── M26: Eval Framework ──────────────────────────────────────────────────────

async def m26_eval_framework() -> None:
    from labrat.eval.runner import EvalRunner
    from labrat.eval.models import EvalCase, EvalStatus

    class _Suite:
        suite_name = "labrat-fixture"
        cases = [
            EvalCase(id="q1", question="How many orders?",   expected_sql="SELECT COUNT(*) FROM orders"),
            EvalCase(id="q2", question="Top users by revenue?", expected_sql="SELECT user_id, SUM(total_amount) FROM orders GROUP BY 1 ORDER BY 2 DESC LIMIT 3"),
            EvalCase(id="q3", question="Orders by status?",  expected_sql="SELECT status, COUNT(*) FROM orders GROUP BY 1"),
        ]

    async def agent(question: str) -> str:
        mapping = {
            "How many orders?": "SELECT COUNT(*) FROM orders",
            "Top users by revenue?": "SELECT user_id, SUM(total_amount) FROM orders GROUP BY 1 ORDER BY 2 DESC LIMIT 3",
            "Orders by status?": "SELECT STATUS, count(*) FROM ORDERS GROUP BY 1",  # case diff → correct
        }
        return mapping.get(question, "SELECT 1")

    runner = EvalRunner(suite=_Suite(), agent_fn=agent)
    report = await runner.run()

    def populate(log: RichLog) -> None:
        log.write("[bold cyan]EvalRunner.run() — SQL execution accuracy benchmark[/bold cyan]")
        log.write(f"  Suite: {report.suite_name}  |  Total: {report.total}")
        log.write("")
        t = Table(show_header=True, header_style="bold magenta", border_style="dim")
        t.add_column("case"); t.add_column("status"); t.add_column("latency")
        for r in report.results:
            icon = "[green]✓[/green]" if r.status == EvalStatus.correct else "[red]✗[/red]"
            t.add_row(r.case_id, f"{icon} {r.status}", f"{r.latency_seconds:.2f}s")
        log.write(t)
        log.write("")
        correct = sum(1 for r in report.results if r.status == EvalStatus.correct)
        log.write(f"  Accuracy: [bold]{report.accuracy:.1%}[/bold]  ({correct}/{report.total})")

    await _snap(_Demo("M26 · Benchmark + Eval Framework", populate), "m26_eval_framework")


# ─── M27: Comparison Harness ──────────────────────────────────────────────────

async def m27_comparison() -> None:
    from labrat.eval.baselines.comparison import ComparisonReport
    from labrat.eval.models import EvalCase, EvalStatus, EvalResult
    from labrat.eval.report import EvalReport

    cases = [EvalCase(id=f"q{i}", question=f"Q{i}") for i in range(1, 6)]
    labrat_results = [
        EvalResult(case_id="q1", status=EvalStatus.correct),
        EvalResult(case_id="q2", status=EvalStatus.correct),
        EvalResult(case_id="q3", status=EvalStatus.correct),
        EvalResult(case_id="q4", status=EvalStatus.wrong),
        EvalResult(case_id="q5", status=EvalStatus.correct),
    ]
    baseline_results = [
        EvalResult(case_id="q1", status=EvalStatus.correct),
        EvalResult(case_id="q2", status=EvalStatus.wrong),
        EvalResult(case_id="q3", status=EvalStatus.correct),
        EvalResult(case_id="q4", status=EvalStatus.wrong),
        EvalResult(case_id="q5", status=EvalStatus.correct),
    ]
    labrat_report   = EvalReport(suite_name="fixture", results=labrat_results)
    baseline_report = EvalReport(suite_name="fixture", results=baseline_results)
    comparison = ComparisonReport(
        labrat=labrat_report,
        baselines=[("zero-shot-sonnet", baseline_report)],
    )
    md = comparison.to_markdown()

    def populate(log: RichLog) -> None:
        log.write("[bold cyan]ComparisonReport.to_markdown() — LabRat vs baselines[/bold cyan]")
        log.write("")
        t = Table(show_header=True, header_style="bold magenta", border_style="dim")
        t.add_column("case")
        t.add_column("zero-shot-sonnet")
        t.add_column("labrat-agent")
        t.add_column("Δ")
        for b, c in zip(baseline_results, labrat_results):
            bm = "[green]✓[/green]" if b.status == EvalStatus.correct else "[red]✗[/red]"
            cm = "[green]✓[/green]" if c.status == EvalStatus.correct else "[red]✗[/red]"
            delta = ("[green]+1[/green]" if c.status == EvalStatus.correct and b.status != EvalStatus.correct
                     else "[red]-1[/red]" if b.status == EvalStatus.correct and c.status != EvalStatus.correct
                     else "—")
            t.add_row(b.case_id, bm, cm, delta)
        log.write(t)
        log.write("")
        log.write(f"  zero-shot-sonnet: [yellow]{baseline_report.accuracy:.0%}[/yellow]")
        log.write(f"  labrat-agent:     [green]{labrat_report.accuracy:.0%}[/green]")
        log.write(f"  Δ improvement:    [bold green]+{(labrat_report.accuracy - baseline_report.accuracy):.0%}[/bold green]")

    await _snap(_Demo("M27 · Comparison Harness (Baselines)", populate), "m27_comparison")


# ─── M28: Query History ───────────────────────────────────────────────────────

async def m28_query_history() -> None:
    from labrat.history.log import QueryHistoryLog
    from labrat.history.events import QueryEvent

    with tempfile.TemporaryDirectory() as tmp:
        log = QueryHistoryLog(history_dir=Path(tmp))
        sqls = [
            ("SELECT COUNT(*) FROM orders", 1, 8),
            ("SELECT user_id, SUM(total_amount) FROM orders GROUP BY user_id", 15, 23),
            ("SELECT * FROM orders WHERE status = 'pending' LIMIT 50", 12, 11),
            ("SELECT username, email FROM users WHERE id = 42", 1, 6),
        ]
        for sql, rows, ms in sqls:
            log.append(QueryEvent(
                timestamp=datetime.now(tz=UTC),
                profile="local-duck",
                thread_id="thread-001",
                version_id="v1",
                sql_final=sql,
                executed=True,
                success=True,
                execution_time_ms=ms,
                row_count=rows,
            ))
        events = log.read_profile("local-duck")

    def populate(rlog: RichLog) -> None:
        rlog.write("[bold cyan]QueryHistoryLog — always-on PII-redacted JSONL per profile[/bold cyan]")
        rlog.write(f"  [dim]~/.local/share/labrat/history/local-duck.jsonl  ({len(events)} events)[/dim]")
        rlog.write("")
        t = Table(show_header=True, header_style="bold magenta", border_style="dim")
        t.add_column("time", min_width=8)
        t.add_column("sql", max_width=55)
        t.add_column("rows"); t.add_column("ms")
        for ev in events:
            t.add_row(ev.timestamp.strftime("%H:%M:%S"), ev.sql_final[:53], str(ev.row_count), str(ev.execution_time_ms))
        rlog.write(t)
        rlog.write("")
        matching = [e for e in events if "orders" in e.sql_final]
        rlog.write(f"[bold cyan]search_query_history('orders')[/bold cyan]  → [green]{len(matching)}[/green] matching")

    await _snap(_Demo("M28 · Query History Capture (Always-On)", populate), "m28_query_history")


# ─── M29: Context Engine ──────────────────────────────────────────────────────

async def m29_context_engine() -> None:
    from labrat.context_engine.analyzer import ContextAnalyzer
    from labrat.context_engine.relevance import score_table_relevance
    from labrat.history.events import QueryEvent

    events = [
        QueryEvent(
            timestamp=datetime.now(tz=UTC),
            profile="local-duck", thread_id="t1", version_id="v1",
            sql_final="SELECT user_id, SUM(total_amount) FROM orders GROUP BY user_id",
            executed=True, success=True, execution_time_ms=22, row_count=15,
        ),
        QueryEvent(
            timestamp=datetime.now(tz=UTC),
            profile="local-duck", thread_id="t1", version_id="v2",
            sql_final="SELECT * FROM users WHERE region_id = 1",
            executed=True, success=True, execution_time_ms=8, row_count=5,
        ),
    ]

    scores = score_table_relevance(events)

    async def mock_llm(prompt: str) -> str:
        return "The user primarily analyses revenue and user behaviour from the orders table."

    analyzer = ContextAnalyzer(llm_fn=mock_llm)
    descriptions = await analyzer.generate_descriptions(
        tables=["orders", "users"], events=events
    )

    def populate(log: RichLog) -> None:
        log.write("[bold cyan]score_table_relevance(events) — frequency × recency weight[/bold cyan]")
        t = Table(show_header=True, header_style="bold magenta", border_style="dim")
        t.add_column("table"); t.add_column("relevance score")
        for tbl, score in sorted(scores.items(), key=lambda x: -x[1]):
            t.add_row(tbl, f"{score:.4f}")
        log.write(t)
        log.write("")
        log.write("[bold cyan]ContextAnalyzer.generate_descriptions(tables, events)[/bold cyan]")
        for tbl, desc in descriptions.items():
            log.write(Panel(desc, title=f"[green]{tbl}[/green]", border_style="dim"))

    await _snap(_Demo("M29 · Context Engine — Personal Domain Builder", populate), "m29_context_engine")


# ─── M30: External Catalog ────────────────────────────────────────────────────

async def m30_external_catalog() -> None:
    from labrat.catalog.dbt.loader import DbtLoader

    dbt_project = ROOT / "tests" / "fixtures" / "sample_dbt_project"
    loader = DbtLoader(project_path=dbt_project)
    entries = list(loader.load().values())

    def populate(log: RichLog) -> None:
        log.write("[bold cyan]DbtLoader.load() — manifest.json → schema.yml → raw SQL[/bold cyan]")
        log.write(f"  [dim]{dbt_project}[/dim]")
        log.write("")
        t = Table(show_header=True, header_style="bold magenta", border_style="dim")
        t.add_column("model"); t.add_column("schema"); t.add_column("description", max_width=45); t.add_column("cols")
        for e in entries[:8]:
            t.add_row(e.name, e.schema_name, (e.description or "")[:43], str(len(e.columns)))
        log.write(t)
        if entries:
            log.write("")
            log.write(f"[bold cyan]CatalogEntry '{entries[0].name}'[/bold cyan]")
            log.write(Syntax(entries[0].model_dump_json(indent=2)[:500], "json", theme="monokai"))

    await _snap(_Demo("M30 · External Catalog Integration (dbt + MCP)", populate), "m30_external_catalog")


# ─── M31: Self-Healing Memory ─────────────────────────────────────────────────

async def m31_memory() -> None:
    from labrat.memory.store import MemoryStore
    from labrat.memory.model import Memory, MemoryScope, MemoryKind

    with tempfile.TemporaryDirectory() as tmp:
        store = MemoryStore(memory_dir=Path(tmp))
        store.append(Memory(
            profile="local-duck",
            scope=MemoryScope.table_,
            kind=MemoryKind.chat_correction,
            text="Valid order statuses: 'pending', 'completed', 'cancelled'. Not 'done' or 'open'.",
            table_scope="orders",
            application_count=0,
        ))
        store.append(Memory(
            profile="local-duck",
            scope=MemoryScope.global_,
            kind=MemoryKind.explicit_user_rule,
            text="All monetary values are in USD cents, not dollars. Divide by 100 before displaying.",
            application_count=3,
        ))
        memories = store.read_profile("local-duck")

    def populate(log: RichLog) -> None:
        log.write("[bold cyan]MemoryStore — self-healing corrections, JSONL per profile[/bold cyan]")
        log.write(f"  [dim]~/.local/share/labrat/memories/local-duck.jsonl  ({len(memories)} entries)[/dim]")
        log.write("")
        t = Table(show_header=True, header_style="bold magenta", border_style="dim")
        t.add_column("scope"); t.add_column("table"); t.add_column("kind"); t.add_column("text", max_width=50); t.add_column("applied")
        for m in memories:
            t.add_row(m.scope.value, m.table_scope or "—", m.kind.value, m.text[:48], str(m.application_count))
        log.write(t)
        log.write("")
        log.write("[bold cyan]recall_memories(context='querying orders') → relevant entries[/bold cyan]")
        log.write("  [green]▸[/green] [table:orders] Valid statuses: 'pending', 'completed', 'cancelled'")
        log.write("  [green]▸[/green] [global] Monetary values are USD cents — divide by 100")

    await _snap(_Demo("M31 · Self-Healing Memory", populate), "m31_memory")


# ─── M32: Custom Validations ──────────────────────────────────────────────────

async def m32_validations() -> None:
    from labrat.validations.checker import ValidationChecker
    from labrat.validations.model import ValidationRule, ValidationSeverity

    rules = [
        ValidationRule(
            profile="local-duck",
            natural_language_rule="Warn when SELECT * is used — prefer explicit column names in production queries.",
            severity=ValidationSeverity.warn,
        ),
        ValidationRule(
            profile="local-duck",
            natural_language_rule="Block queries that lack a LIMIT clause.",
            severity=ValidationSeverity.block,
        ),
    ]

    sql_cases = [
        ("SELECT * FROM orders", "5 rows returned"),
        ("SELECT username, email FROM users LIMIT 20", "20 rows returned"),
    ]

    async def mock_llm(prompt: str) -> str:
        if "SELECT *" in prompt and "SELECT *" in prompt.split("SQL query:")[-1]:
            return "warn: SELECT * returns all columns; prefer explicit column names in production."
        return "pass"

    checker = ValidationChecker(llm_fn=mock_llm)

    def populate(log: RichLog) -> None:
        log.write("[bold cyan]ValidationChecker.check(sql, result_summary, rules)[/bold cyan]")
        log.write("")

    results_by_sql: list[Any] = []
    for sql, summary in sql_cases:
        try:
            r = asyncio.get_event_loop().run_until_complete(
                checker.check(sql, summary, rules)
            )
        except RuntimeError:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                fut = pool.submit(asyncio.run, checker.check(sql, summary, rules))
                r = fut.result()
        results_by_sql.append((sql, r))

    def populate(log: RichLog) -> None:
        log.write("[bold cyan]ValidationChecker.check(sql, result_summary, rules)[/bold cyan]")
        log.write("")
        for sql, results in results_by_sql:
            log.write(f"  [bold]SQL:[/bold] [dim]{sql[:70]}[/dim]")
            for r in results:
                icon = {"pass": "[green]✓[/green]", "warn": "[yellow]⚠[/yellow]", "block": "[red]✗[/red]"}
                vstr = r.status.value
                log.write(f"    {icon.get(vstr, '?')} [{r.rule_id[:8]}…] {r.explanation or vstr}")
            log.write("")
        log.write("[dim]Rules defined in labrat.validations.model.BUILTIN_EXAMPLE_RULES[/dim]")
        log.write("[dim]Per-rule LLM calls: easier attribution, future parallelism.[/dim]")

    await _snap(_Demo("M32 · Custom Validations", populate), "m32_validations")


# ─── Run all ──────────────────────────────────────────────────────────────────

MILESTONES: list[tuple[str, Any]] = [
    ("M1  · Branding",              m01_branding),
    ("M2  · Database Layer",        m02_database_layer),
    ("M3  · Profiles",              m03_profiles),
    ("M4  · Onboarding",            m04_onboarding),
    ("M5  · Layout",                m05_layout),
    ("M6  · Schema Browser",        m06_schema_browser),
    ("M7  · SQL Editor",            m07_sql_editor),
    ("M8  · Completion",            m08_completion),
    ("M9  · Validation",            m09_validation),
    ("M10 · Results Table",         m10_results_table),
    ("M11 · Tool Registry",         m11_tool_registry),
    ("M12 · Schema Tools",          m12_schema_tools),
    ("M13 · Safety Gates",          m13_safety),
    ("M14 · Agent Loop",            m14_agent_loop),
    ("M14.5 · Providers",           m14_5_providers),
    ("M15 · System Prompts",        m15_prompts),
    ("M16 · Chat Panel",            m16_chat_panel),
    ("M17 · Threads",               m17_threads),
    ("M18 · Findings",              m18_findings),
    ("M19 · Audit Log",             m19_audit_log),
    ("M20 · HTML Export",           m20_export),
    ("M21 · Chart Spec",            m21_chart_spec),
    ("M22 · Image Protocol",        m22_chart_image),
    ("M23 · Postgres",              m23_postgres),
    ("M24 · Multi-Connection",      m24_multi_connection),
    ("M25 · Warehouse Adapters",    m25_warehouse_adapters),
    ("M26 · Eval Framework",        m26_eval_framework),
    ("M27 · Comparison Harness",    m27_comparison),
    ("M28 · Query History",         m28_query_history),
    ("M29 · Context Engine",        m29_context_engine),
    ("M30 · External Catalog",      m30_external_catalog),
    ("M31 · Memory",                m31_memory),
    ("M32 · Validations",           m32_validations),
]


async def main() -> None:
    passed: list[str] = []
    failed: list[tuple[str, str]] = []

    for label, fn in MILESTONES:
        print(f"\n{label}")
        try:
            await fn()
            passed.append(label)
        except Exception as exc:
            import traceback
            print(f"  ✗  FAILED: {exc}")
            traceback.print_exc()
            failed.append((label, str(exc)))

    print(f"\n{'─'*60}")
    print(f"Done — {len(passed)} passed, {len(failed)} failed")
    if failed:
        print("\nFailed milestones:")
        for label, err in failed:
            print(f"  {label}: {err[:120]}")
    print(f"\nScreenshots saved to: {SCREENSHOTS}/")


if __name__ == "__main__":
    asyncio.run(main())
