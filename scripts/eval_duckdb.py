"""Evaluate LabRat's NL→SQL pipeline against the sample ecommerce DuckDB.

This script tests:
1. Schema introspection (no API key needed)
2. SQL execution via DuckDBConnection
3. Full NL→SQL eval (requires ANTHROPIC_API_KEY)

Run with: uv run python scripts/eval_duckdb.py

Results are written to eval_output/duckdb_eval.md
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "tests/fixtures/sample_dbs/ecommerce.duckdb"
OUTPUT_DIR = Path(__file__).parent.parent / "eval_output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ── Test questions + expected SQL keywords ─────────────────────────────────────

EVAL_CASES = [
    {
        "id": "q01",
        "question": "What is the total revenue from completed orders?",
        "expected_result_contains": "total",
        "gold_sql": (
            "SELECT SUM(total_amount) AS total_revenue FROM orders WHERE status = 'completed'"
        ),
        "gold_answer": 7225.50,
    },
    {
        "id": "q02",
        "question": "Show me revenue by region for Q4 2025 (completed orders only)",
        "expected_result_contains": "region",
        "gold_sql": (
            "SELECT region, SUM(total_amount) AS revenue "
            "FROM orders "
            "WHERE status = 'completed' "
            "  AND created_at >= '2025-10-01' AND created_at < '2026-01-01' "
            "GROUP BY region ORDER BY revenue DESC"
        ),
        "gold_answer": {"EU": 3850.0, "US-West": 2200.25, "US-East": 1100.0},
    },
    {
        "id": "q03",
        "question": "Who are the top 3 customers by total spend?",
        "expected_result_contains": "customer",
        "gold_sql": (
            "SELECT c.name, SUM(o.total_amount) AS total_spend "
            "FROM orders o JOIN customers c ON o.customer_id = c.customer_id "
            "WHERE o.status = 'completed' "
            "GROUP BY c.name ORDER BY total_spend DESC LIMIT 3"
        ),
        "gold_answer": "Alice Johnson",
    },
    {
        "id": "q04",
        "question": "How many orders are in each status?",
        "expected_result_contains": "status",
        "gold_sql": (
            "SELECT status, COUNT(*) AS count FROM orders GROUP BY status ORDER BY count DESC"
        ),
        "gold_answer": {"completed": 6, "pending": 1, "refunded": 1},
    },
    {
        "id": "q05",
        "question": "What is the average order value by product category?",
        "expected_result_contains": "category",
        "gold_sql": (
            "SELECT p.category, AVG(o.total_amount) AS avg_order_value "
            "FROM orders o JOIN products p ON o.product_id = p.product_id "
            "GROUP BY p.category ORDER BY avg_order_value DESC"
        ),
        "gold_answer": "Hardware",
    },
]


# ── Schema introspection test (no API key) ─────────────────────────────────────


def test_schema_introspection() -> dict[str, object]:
    from labrat.db.duckdb_engine import DuckDBConnection

    conn = DuckDBConnection(str(DB_PATH), read_only=True)
    conn.connect()
    try:
        catalog = conn.introspect_catalog()
        tables = {t.name for schema in catalog.schemas for t in schema.tables}
        return {
            "status": "pass",
            "database": catalog.database_name,
            "tables": sorted(tables),
            "schema_count": len(catalog.schemas),
        }
    finally:
        conn.disconnect()


def test_sql_execution() -> dict[str, object]:
    from labrat.db.duckdb_engine import DuckDBConnection

    conn = DuckDBConnection(str(DB_PATH), read_only=True)
    conn.connect()
    try:
        df = conn.execute(
            "SELECT region, SUM(total_amount) AS revenue "
            "FROM orders WHERE status = 'completed' "
            "GROUP BY region ORDER BY revenue DESC"
        )
        return {
            "status": "pass",
            "rows": len(df),
            "columns": df.columns,
            "top_region": df["region"][0] if len(df) > 0 else None,
        }
    finally:
        conn.disconnect()


def test_gold_sql_correctness() -> list[dict[str, object]]:
    """Run the gold SQL for each eval case and capture the result."""
    from labrat.db.duckdb_engine import DuckDBConnection

    conn = DuckDBConnection(str(DB_PATH), read_only=True)
    conn.connect()
    results = []
    try:
        for case in EVAL_CASES:
            t0 = time.monotonic()
            try:
                df = conn.execute(case["gold_sql"])
                latency_ms = (time.monotonic() - t0) * 1000
                results.append(
                    {
                        "id": case["id"],
                        "question": case["question"],
                        "status": "pass",
                        "rows": len(df),
                        "columns": df.columns,
                        "latency_ms": round(latency_ms, 1),
                        "sample": df.head(3).to_dicts(),
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "id": case["id"],
                        "question": case["question"],
                        "status": "error",
                        "error": str(exc),
                    }
                )
    finally:
        conn.disconnect()
    return results


# ── Full agent eval (API key or claude CLI) ────────────────────────────────────


def _make_provider() -> object:
    """Return the best available provider: API key > claude CLI."""
    import shutil

    if os.environ.get("ANTHROPIC_API_KEY"):
        from labrat.agent.providers.anthropic_direct import AnthropicProvider

        return AnthropicProvider(model="claude-haiku-4-5-20251001")
    if shutil.which("claude"):
        from labrat.agent.providers.claude_code import ClaudeCodeProvider

        return ClaudeCodeProvider()
    raise RuntimeError("No provider available: set ANTHROPIC_API_KEY or install the claude CLI")


async def test_agent_nl_to_sql() -> list[dict[str, object]]:
    """Run each question through LabRat's agent and compare to gold SQL."""

    from labrat.agent.loop import AgentLoop
    from labrat.agent.tools.describe_table import DescribeTableTool
    from labrat.agent.tools.list_tables import ListTablesTool
    from labrat.agent.tools.run_sql import RunSqlTool
    from labrat.agent.tools.sample_rows import SampleRowsTool
    from labrat.db.duckdb_engine import DuckDBConnection

    conn = DuckDBConnection(str(DB_PATH), read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()

    provider = _make_provider()
    tools = [
        ListTablesTool(),
        DescribeTableTool(),
        SampleRowsTool(),
        RunSqlTool(),
    ]

    results = []
    for case in EVAL_CASES:
        t0 = time.monotonic()
        try:
            loop = AgentLoop(
                provider=provider,
                tools=tools,
                connection=conn,
                catalog=catalog,
            )
            generated_sql = await loop.run(case["question"])
            latency = time.monotonic() - t0

            # Compare generated SQL to gold
            df_gold = conn.execute(case["gold_sql"])
            try:
                df_gen = conn.execute(generated_sql)
                rows_match = len(df_gen) == len(df_gold)
            except Exception:
                df_gen = None
                rows_match = False

            results.append(
                {
                    "id": case["id"],
                    "question": case["question"],
                    "generated_sql": generated_sql,
                    "gold_sql": case["gold_sql"],
                    "status": "correct" if rows_match else "wrong",
                    "latency_s": round(latency, 2),
                    "gold_rows": len(df_gold),
                    "gen_rows": len(df_gen) if df_gen is not None else None,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "id": case["id"],
                    "question": case["question"],
                    "status": "error",
                    "error": str(exc),
                    "latency_s": round(time.monotonic() - t0, 2),
                }
            )

    conn.disconnect()
    return results


# ── Report generation ──────────────────────────────────────────────────────────


def write_report(
    schema_result: dict[str, object],
    exec_result: dict[str, object],
    gold_results: list[dict[str, object]],
    agent_results: list[dict[str, object]] | None,
) -> Path:
    lines = [
        "# LabRat DuckDB Evaluation Report",
        "",
        "## Environment",
        f"- Database: `{DB_PATH.name}`",
        f"- API key available: {'Yes' if os.environ.get('ANTHROPIC_API_KEY') else 'No'}",
        "",
        "## 1. Schema Introspection",
        "",
        f"**Status:** {schema_result.get('status', '?')}",
        f"- Database: `{schema_result.get('database')}`",
        f"- Tables found: {schema_result.get('tables')}",
        f"- Schemas: {schema_result.get('schema_count')}",
        "",
        "## 2. SQL Execution",
        "",
        f"**Status:** {exec_result.get('status', '?')}",
        f"- Rows returned: {exec_result.get('rows')}",
        f"- Columns: {exec_result.get('columns')}",
        f"- Top region by revenue: {exec_result.get('top_region')}",
        "",
        "## 3. Gold SQL Correctness",
        "",
        "Runs the hand-written gold SQL for each eval case to confirm the database is correct.",
        "",
        "| ID | Question | Status | Rows | Latency |",
        "|---|---|---|---|---|",
    ]
    for r in gold_results:
        status = "✅" if r.get("status") == "pass" else "❌"
        q = str(r.get("question", ""))[:60]
        lines.append(
            f"| {r['id']} | {q} | {status} | {r.get('rows', '?')} | {r.get('latency_ms', '?')}ms |"
        )

    if agent_results:
        correct = sum(1 for r in agent_results if r.get("status") == "correct")
        total = len(agent_results)
        lines += [
            "",
            "## 4. Agent NL→SQL Evaluation",
            "",
            f"**Accuracy: {correct}/{total} ({100 * correct // total}%)**",
            "",
            "| ID | Question | Status | Gold Rows | Gen Rows | Latency |",
            "|---|---|---|---|---|---|",
        ]
        for r in agent_results:
            status_icon = {"correct": "✅", "wrong": "❌", "error": "💥"}.get(
                str(r.get("status")), "?"
            )
            q = str(r.get("question", ""))[:60]
            lines.append(
                f"| {r['id']} | {q} | {status_icon} "
                f"| {r.get('gold_rows', '?')} | {r.get('gen_rows', '?')} "
                f"| {r.get('latency_s', '?')}s |"
            )
            if r.get("generated_sql"):
                lines += [
                    "<details><summary>Generated SQL</summary>",
                    "",
                    "```sql",
                    str(r["generated_sql"]),
                    "```",
                    "</details>",
                    "",
                ]
    else:
        lines += [
            "",
            "## 4. Agent NL→SQL Evaluation",
            "",
            "> ⚠️ Skipped — no provider available.",
            "> Set `ANTHROPIC_API_KEY` or install the claude CLI.",
        ]

    path = OUTPUT_DIR / "duckdb_eval.md"
    path.write_text("\n".join(lines) + "\n")
    return path


async def main() -> None:
    print("LabRat DuckDB Evaluation")
    print("=" * 40)

    print("\n[1/3] Schema introspection...")
    schema_result = test_schema_introspection()
    print(f"  {schema_result['status'].upper()}: found {len(schema_result['tables'])} tables")

    print("\n[2/3] SQL execution test...")
    exec_result = test_sql_execution()
    top = exec_result["top_region"]
    print(f"  {exec_result['status'].upper()}: {exec_result['rows']} rows, top region = {top}")

    print("\n[3/3] Gold SQL correctness...")
    gold_results = test_gold_sql_correctness()
    passed = sum(1 for r in gold_results if r.get("status") == "pass")
    print(f"  {passed}/{len(gold_results)} cases passed")

    import shutil

    agent_results = None
    has_provider = bool(os.environ.get("ANTHROPIC_API_KEY")) or bool(shutil.which("claude"))
    if has_provider:
        provider_name = "API key" if os.environ.get("ANTHROPIC_API_KEY") else "claude CLI"
        print(f"\n[4/4] Agent NL→SQL evaluation (via {provider_name})...")
        agent_results = await test_agent_nl_to_sql()
        correct = sum(1 for r in agent_results if r.get("status") == "correct")
        print(f"  Agent accuracy: {correct}/{len(agent_results)}")
    else:
        print("\n[4/4] Agent eval: SKIPPED (no ANTHROPIC_API_KEY or claude CLI)")

    path = write_report(schema_result, exec_result, gold_results, agent_results)
    print(f"\nReport written → {path}")


if __name__ == "__main__":
    asyncio.run(main())
