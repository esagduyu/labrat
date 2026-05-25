#!/usr/bin/env python3
"""
Live tour screenshot generator for LabRat feature showcase.

Connects to the real ecommerce.duckdb and drives the full MainScreen.
The agent runs FIRST so every subsequent screenshot shows a live, populated UI.

Usage:
    uv run scripts/tour_screenshots.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

FIXTURE_DB = ROOT / "tests" / "fixtures" / "sample_dbs" / "ecommerce.duckdb"
OUT = ROOT / "screenshots" / "tour"
OUT.mkdir(parents=True, exist_ok=True)

SIZE = (220, 50)  # wide terminal for the 3-pane layout


async def main() -> None:
    from labrat.db.catalog_cache import load_cached_catalog, save_catalog
    from labrat.db.duckdb_engine import DuckDBConnection
    from labrat.screens.main import MainScreen

    conn = DuckDBConnection(FIXTURE_DB, read_only=True)
    conn.connect()
    catalog = load_cached_catalog("egetest")
    if catalog is None:
        catalog = conn.introspect_catalog()
        save_catalog("egetest", catalog)

    from textual.app import App

    class TourApp(App[None]):
        def on_mount(self) -> None:
            self.push_screen(
                MainScreen(
                    profile="egetest",
                    dialect="duckdb",
                    catalog=catalog,
                    connection=conn,
                )
            )

    tour = TourApp()

    async def run_tour(pilot) -> None:  # type: ignore[no-untyped-def]
        await pilot.pause(1.0)

        # ── 1. Agent answers a question — the hero moment ────────────────────
        # Focus chat and ask a real question so the agent runs live
        await pilot.press("ctrl+1")
        await pilot.pause(0.3)
        question = "How many orders are in the database? What is the total amount?"
        for ch in question:
            if ch == " ":
                await pilot.press("space")
            else:
                await pilot.press(ch)
        await pilot.pause(0.3)
        await pilot.press("enter")
        print("  ⏳  waiting for agent response (up to 30s)…")
        await pilot.pause(30.0)
        snap("01_agent_response", pilot)

        # ── 2. Three-pane overview after agent ran ───────────────────────────
        # Return focus to editor — shows all three panes populated
        await pilot.press("ctrl+2")
        await pilot.pause(0.3)
        snap("02_main_layout", pilot)

        # ── 3. SQL the agent wrote (editor pane) ────────────────────────────
        await pilot.press("ctrl+2")
        await pilot.pause(0.3)
        snap("03_sql_editor", pilot)

        # ── 4. Results table after the agent's query ─────────────────────────
        await pilot.press("ctrl+3")
        await pilot.pause(0.3)
        snap("04_results_table", pilot)

        # ── 5. Schema browser (right pane) ──────────────────────────────────
        await pilot.press("ctrl+4")
        await pilot.pause(0.3)
        snap("05_schema_browser", pilot)

        # ── 6. Thread manager (Ctrl+T) ───────────────────────────────────────
        await pilot.press("ctrl+t")
        await pilot.pause(0.5)
        snap("06_thread_manager", pilot)
        await pilot.press("escape")
        await pilot.pause(0.3)

        # ── 7. Findings viewer (Ctrl+K) ──────────────────────────────────────
        await pilot.press("ctrl+k")
        await pilot.pause(0.5)
        snap("07_findings_viewer", pilot)
        await pilot.press("escape")
        await pilot.pause(0.3)

        # ── 8. History browser (Ctrl+R) ──────────────────────────────────────
        await pilot.press("ctrl+r")
        await pilot.pause(0.5)
        snap("08_history_browser", pilot)
        await pilot.press("escape")
        await pilot.pause(0.3)

        # ── 9. Memories viewer (Ctrl+G) ──────────────────────────────────────
        await pilot.press("ctrl+g")
        await pilot.pause(0.5)
        snap("09_memories_viewer", pilot)
        await pilot.press("escape")
        await pilot.pause(0.3)

        # ── 10. Help screen ──────────────────────────────────────────────────
        await pilot.press("f1")
        await pilot.pause(0.5)
        snap("10_help_screen", pilot)
        await pilot.press("escape")
        await pilot.pause(0.3)

        # ── 11. Schema hidden (focus mode) ───────────────────────────────────
        await pilot.press("ctrl+h")
        await pilot.pause(0.3)
        snap("11_schema_hidden", pilot)
        await pilot.press("ctrl+h")  # restore
        await pilot.pause(0.3)

        # ── 12. Final overview ───────────────────────────────────────────────
        await pilot.press("ctrl+2")
        await pilot.pause(0.3)
        snap("12_final_overview", pilot)

        pilot.app.exit()

    def snap(name: str, pilot) -> None:  # type: ignore[no-untyped-def]
        path = str(OUT / f"{name}.svg")
        pilot.app.save_screenshot(path)
        print(f"  ✓  {name}.svg")

    async with tour.run_test(size=SIZE) as pilot:
        await run_tour(pilot)

    print(f"\n✅  {len(list(OUT.glob('*.svg')))} screenshots saved to {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
