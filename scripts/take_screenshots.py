"""Generate SVG screenshots of LabRat UI components.

Run with: uv run python scripts/take_screenshots.py

Saves screenshots to screenshots/ directory.
No API key required — uses Textual's headless pilot.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import polars as pl
from textual.app import App, ComposeResult

SCREENSHOTS_DIR = Path(__file__).parent.parent / "screenshots"
SCREENSHOTS_DIR.mkdir(exist_ok=True)


async def screenshot_widget(
    app: App[None],
    filename: str,
    pause_seconds: float = 0.4,
) -> None:
    """Run app in headless mode and save screenshot."""
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause(pause_seconds)
        svg = app.export_screenshot()
        path = SCREENSHOTS_DIR / filename
        path.write_text(svg)
        print(f"  saved → {path.name}")


# ── Screenshots ────────────────────────────────────────────────────────────────


async def screenshot_results_table() -> None:
    from labrat.widgets.results_table import ResultsTable

    df = pl.DataFrame(
        {
            "order_id": [1, 2, 3, 4, 5],
            "customer": ["alice", "bob", "charlie", "dave", "eve"],
            "region": ["US-W", "US-E", "EU", "APAC", "US-W"],
            "total_amount": [1250.00, 87.50, 3400.00, 220.75, 950.25],
            "status": ["completed", "pending", "completed", "refunded", "completed"],
        }
    )

    class _App(App[None]):
        CSS = "ResultsTable { height: 1fr; }"

        def compose(self) -> ComposeResult:
            rt = ResultsTable()
            rt.load(df, execution_time=0.042)
            yield rt

    await screenshot_widget(_App(), "results_table.svg")


async def screenshot_schema_browser() -> None:
    from labrat.db.catalog import Catalog, Column, Schema, Table
    from labrat.widgets.schema_tree import SchemaTree

    def _col(name: str, dtype: str) -> Column:
        return Column(name=name, data_type=dtype, nullable=True)

    catalog = Catalog(
        database_name="labrat_demo",
        schemas=[
            Schema(
                name="public",
                tables=[
                    Table(
                        name="orders",
                        schema_name="public",
                        columns=[
                            _col("order_id", "INTEGER"),
                            _col("customer_id", "INTEGER"),
                            _col("total_amount", "DECIMAL"),
                            _col("status", "VARCHAR"),
                            _col("created_at", "TIMESTAMP"),
                        ],
                    ),
                    Table(
                        name="customers",
                        schema_name="public",
                        columns=[
                            _col("customer_id", "INTEGER"),
                            _col("name", "VARCHAR"),
                            _col("email", "VARCHAR"),
                            _col("region", "VARCHAR"),
                        ],
                    ),
                    Table(
                        name="products",
                        schema_name="public",
                        columns=[
                            _col("product_id", "INTEGER"),
                            _col("name", "VARCHAR"),
                            _col("price", "DECIMAL"),
                            _col("category", "VARCHAR"),
                        ],
                    ),
                ],
            ),
            Schema(
                name="staging",
                tables=[
                    Table(
                        name="stg_orders",
                        schema_name="staging",
                        columns=[
                            _col("order_id", "INTEGER"),
                            _col("raw_status", "VARCHAR"),
                        ],
                    ),
                ],
            ),
        ],
    )

    class _App(App[None]):
        CSS = "SchemaTree { height: 1fr; }"

        def compose(self) -> ComposeResult:
            yield SchemaTree(catalog=catalog)

    await screenshot_widget(_App(), "schema_browser.svg")


async def screenshot_sql_editor() -> None:
    from labrat.widgets.query_editor import QueryEditor

    sample_sql = (
        "SELECT\n"
        "    r.name          AS region,\n"
        "    COUNT(*)        AS order_count,\n"
        "    SUM(o.total_amount)  AS revenue,\n"
        "    AVG(o.total_amount)  AS avg_order_value\n"
        "FROM public.orders o\n"
        "JOIN public.regions r\n"
        "    ON o.region_id = r.id\n"
        "WHERE o.status = 'completed'\n"
        "  AND o.created_at BETWEEN '2025-10-01' AND '2025-12-31'\n"
        "GROUP BY r.name\n"
        "ORDER BY revenue DESC\n"
    )

    class _App(App[None]):
        CSS = "QueryEditor { height: 1fr; }"

        def compose(self) -> ComposeResult:
            yield QueryEditor(text=sample_sql)

    await screenshot_widget(_App(), "sql_editor.svg")


async def screenshot_trace_log() -> None:
    from labrat.widgets.trace_log import TraceLog

    class _App(App[None]):
        CSS = "TraceLog { height: 1fr; }"

        def compose(self) -> ComposeResult:
            yield TraceLog()

        def on_mount(self) -> None:
            log = self.query_one(TraceLog)
            log.log_text("▸ checking schema")
            log.log_text("▸ sampling rows from orders (5 rows)")
            log.log_text("▸ found FK: orders.customer_id → customers.customer_id")
            log.log_text("▸ applying memory: 'always filter by status = completed'")
            log.log_text("▸ generating SQL (duckdb dialect)")
            log.log_tool_call("run_sql", {"sql": "SELECT ..."}, "5 rows in 42ms")

    await screenshot_widget(_App(), "trace_log.svg")


async def screenshot_chat_panel() -> None:
    from labrat.widgets.chat_panel import ChatPanel

    class _App(App[None]):
        CSS = "ChatPanel { height: 1fr; width: 1fr; }"

        def compose(self) -> ComposeResult:
            yield ChatPanel()

        def on_mount(self) -> None:
            panel = self.query_one(ChatPanel)
            panel._append_history(
                "[bold blue]You:[/bold blue] show me Q4 revenue by region",
                "You: show me Q4 revenue by region",
            )
            panel._append_history(
                "[bold green]Agent:[/bold green] I'll query orders joined to regions, "
                "filtering to Q4 2025 with status = 'completed'.",
                "Agent: ...",
            )
            panel._append_history(
                "[dim]▸ list_tables() → [orders, customers, regions][/dim]",
                "▸ list_tables()",
            )
            panel._append_history(
                "[dim]▸ run_sql(…) → 5 rows in 42ms[/dim]",
                "▸ run_sql()",
            )

    await screenshot_widget(_App(), "chat_panel.svg")


async def main() -> None:
    print("Taking LabRat UI screenshots...\n")

    tasks = [
        ("Results Table", screenshot_results_table),
        ("Schema Browser", screenshot_schema_browser),
        ("SQL Editor", screenshot_sql_editor),
        ("Trace Log", screenshot_trace_log),
        ("Chat Panel", screenshot_chat_panel),
    ]

    for name, fn in tasks:
        print(f"{name}:")
        try:
            await fn()
        except Exception as exc:
            print(f"  ERROR: {exc}")

    print(f"\nAll screenshots → {SCREENSHOTS_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
