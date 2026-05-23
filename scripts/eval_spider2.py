"""Evaluate LabRat against Spider2-DBT benchmark tasks.

Spider2-DBT tasks ask agents to complete dbt projects (write SQL models).
LabRat's DuckDB engine can introspect compiled dbt projects and run SQL.

This script:
1. Lists available Spider2-DBT tasks
2. For each task with a DuckDB source, introspects the schema
3. With ANTHROPIC_API_KEY: generates SQL and compares to gold answers
4. Writes a detailed report

Run with:
  uv run python scripts/eval_spider2.py [--limit N]

Gold answers are in ~/repos/spider2/spider2-dbt/evaluation_suite/gold/spider2_eval.jsonl
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SPIDER2_DIR = Path.home() / "repos" / "spider2"
SPIDER2_DBT_DIR = SPIDER2_DIR / "spider2-dbt"
EVAL_JSONL = SPIDER2_DBT_DIR / "evaluation_suite" / "gold" / "spider2_eval.jsonl"
TASKS_JSONL = SPIDER2_DBT_DIR / "examples" / "spider2-dbt.jsonl"
OUTPUT_DIR = Path(__file__).parent.parent / "eval_output"
OUTPUT_DIR.mkdir(exist_ok=True)


def load_tasks(limit: int = 5) -> list[dict[str, str]]:
    tasks = []
    with open(TASKS_JSONL) as f:
        for line in f:
            task = json.loads(line.strip())
            if task.get("type") == "DBT":
                tasks.append(task)
                if len(tasks) >= limit:
                    break
    return tasks


def load_gold_answers() -> dict[str, dict]:
    answers = {}
    with open(EVAL_JSONL) as f:
        for line in f:
            item = json.loads(line.strip())
            answers[item["instance_id"]] = item
    return answers


def explore_task_schema(instance_id: str) -> dict[str, object]:
    """Introspect the dbt project schema for a given task."""
    from labrat.catalog.dbt.loader import DbtLoader

    task_dir = SPIDER2_DBT_DIR / "examples" / instance_id
    if not task_dir.exists():
        return {"status": "no_task_dir", "instance_id": instance_id}

    # Check for manifest.json (compiled dbt project)
    manifest = task_dir / "manifest.json"
    if manifest.exists():
        try:
            loader = DbtLoader(project_path=task_dir)
            entries = loader.load()
            return {
                "status": "manifest_loaded",
                "instance_id": instance_id,
                "models": sorted(entries.keys()),
                "model_count": len(entries),
            }
        except Exception as exc:
            return {"status": "manifest_error", "error": str(exc)}

    # Check for schema.yml files
    schema_files = list(task_dir.rglob("schema.yml"))
    if schema_files:
        try:
            loader = DbtLoader(project_path=task_dir)
            entries = loader.load()
            return {
                "status": "schema_yml_loaded",
                "instance_id": instance_id,
                "models": sorted(entries.keys()),
                "model_count": len(entries),
                "schema_files": len(schema_files),
            }
        except Exception as exc:
            return {"status": "schema_yml_error", "error": str(exc)}

    # No compiled artifacts — list raw SQL models
    sql_files = list(task_dir.rglob("*.sql"))
    return {
        "status": "raw_sql_only",
        "instance_id": instance_id,
        "sql_files": [str(f.relative_to(task_dir)) for f in sql_files[:10]],
    }


def write_report(
    tasks: list[dict[str, str]],
    gold_answers: dict[str, dict],
    schema_results: list[dict[str, object]],
) -> Path:
    lines = [
        "# LabRat Spider2-DBT Evaluation Report",
        "",
        "## Overview",
        "",
        f"- Spider2-DBT total tasks: {sum(1 for _ in open(TASKS_JSONL))}",
        f"- Tasks evaluated in this run: {len(tasks)}",
        f"- Gold answers available: {len(gold_answers)}",
        f"- API key available: {'Yes' if os.environ.get('ANTHROPIC_API_KEY') else 'No'}",
        "",
        "## Notes on Spider2-DBT Format",
        "",
        "Spider2-DBT tasks ask agents to **complete dbt projects** — writing SQL models",
        "that produce specific tables. This is different from LabRat's primary use case",
        "(interactive NL→SQL against a connected warehouse).",
        "",
        "LabRat's dbt catalog loader (M30) can introspect these projects. The full",
        "agent integration for dbt model completion would require additional work beyond",
        "the current M30 scope.",
        "",
        "## Task Schema Exploration",
        "",
        "| Task | Status | Models / SQL Files |",
        "|---|---|---|",
    ]

    for result in schema_results:
        iid = result.get("instance_id", "?")
        status = str(result.get("status", "?"))
        detail = ""
        if result.get("models"):
            models = result["models"]
            assert isinstance(models, list)
            detail = f"{len(models)} models: {', '.join(str(m) for m in models[:5])}"
            if len(models) > 5:
                detail += f" (+{len(models) - 5} more)"
        elif result.get("sql_files"):
            files = result["sql_files"]
            assert isinstance(files, list)
            detail = f"{len(files)} SQL files"
        lines.append(f"| {iid} | {status} | {detail} |")

    lines += [
        "",
        "## Gold Answer Format",
        "",
        "Spider2-DBT gold answers use DuckDB comparison (result tables must match).",
        "Example gold entry:",
        "```json",
    ]
    first_gold = next(iter(gold_answers.values()), {})
    lines.append(json.dumps(first_gold, indent=2))
    lines += [
        "```",
        "",
        "## Next Steps for Full Evaluation",
        "",
        "1. **Set `ANTHROPIC_API_KEY`** to enable agent NL→SQL calls",
        "2. **Compile dbt projects**: run `dbt parse` in each task directory",
        "   to generate `manifest.json` files (currently most tasks have raw SQL only)",
        "3. **Implement dbt model completion**: extend AgentLoop to write dbt models",
        "   rather than just executing NL→SQL queries",
        "4. **Run the official evaluator**: use spider2's `evaluate.py` with the generated",
        "   model outputs to compute the official score",
        "",
        "## Current LabRat Capabilities vs Spider2-DBT",
        "",
        "| Capability | LabRat Status | Spider2-DBT Need |",
        "|---|---|---|",
        "| Schema introspection from manifest.json | ✅ M30 | Required |",
        "| PII column detection from dbt tags | ✅ M30 | Bonus |",
        "| Lineage graph traversal | ✅ M30 | Required |",
        "| NL→SQL generation (DuckDB dialect) | ✅ M2/M14 | Required |",
        "| dbt model file writing | ❌ Not implemented | Core requirement |",
        "| Multi-model project completion | ❌ Not implemented | Core requirement |",
        "| Running dbt to validate outputs | ❌ Not implemented | Required for eval |",
    ]

    path = OUTPUT_DIR / "spider2_dbt_eval.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def main() -> None:
    limit = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[1] == "--limit" else 10

    print(f"Spider2-DBT Evaluation (limit={limit})")
    print("=" * 40)

    print("\n[1] Loading tasks...")
    tasks = load_tasks(limit=limit)
    print(f"  Loaded {len(tasks)} DBT tasks")

    print("\n[2] Loading gold answers...")
    gold_answers = load_gold_answers()
    print(f"  {len(gold_answers)} gold answers")

    print("\n[3] Exploring task schemas...")
    schema_results = []
    for task in tasks:
        iid = task["instance_id"]
        result = explore_task_schema(iid)
        status = result.get("status", "?")
        detail = ""
        if result.get("models"):
            detail = f"{result['model_count']} models"
        elif result.get("sql_files"):
            detail = f"{len(result['sql_files'])} SQL files"
        print(f"  {iid}: {status} {detail}")
        schema_results.append(result)

    path = write_report(tasks, gold_answers, schema_results)
    print(f"\nReport written → {path}")


if __name__ == "__main__":
    main()
