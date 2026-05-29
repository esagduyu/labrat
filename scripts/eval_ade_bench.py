#!/usr/bin/env python3
"""Run ADE-bench evaluation against the ade CLI.

Usage:
    uv run scripts/eval_ade_bench.py [--tasks TASK_ID ...] [--agent sage|claude]

Requires:
    - ADE_BENCH_DIR env var pointing to the ade-bench repo checkout
    - Docker running
    - ade CLI installed in the ade-bench venv
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from labrat.eval.benchmarks.ade_bench.suite import AdeBenchSuite
from labrat.eval.reporting import report_to_markdown
from labrat.eval.types import BenchmarkReport, TrialResult


async def run(suite: AdeBenchSuite, task_ids: list[str] | None, n_attempts: int) -> BenchmarkReport:
    tasks = [t for t in suite.tasks() if task_ids is None or t.id in set(task_ids)]
    trials: list[TrialResult] = []
    for task in tasks:
        # Best-of-k: stop early if any attempt passes
        for attempt in range(n_attempts):
            scratch = Path("runs") / "ade_bench" / "scratch" / f"{task.id}__trial{attempt}"
            scratch.mkdir(parents=True, exist_ok=True)
            r = await suite.run_trial(task, attempt, scratch)
            trials.append(r)
            if r.passed:
                break
    score = suite.aggregate(trials)
    return BenchmarkReport(
        benchmark=suite.name,
        run_id="ade-bench-local",
        score=score,
        trials=trials,
        config={"n_attempts": n_attempts, "tasks": task_ids},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ADE-bench evaluation")
    parser.add_argument(
        "--tasks",
        nargs="+",
        metavar="TASK_ID",
        help="Task IDs to run (default: all ready duckdb+dbt tasks)",
    )
    parser.add_argument(
        "--agent",
        default="sage",
        help="ADE-bench agent to use (default: sage)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Override output directory for ade results",
    )
    parser.add_argument(
        "--concurrent",
        type=int,
        default=1,
        help="Number of concurrent trials (default: 1)",
    )
    parser.add_argument(
        "--n-attempts",
        type=int,
        default=1,
        help="Number of attempts per task; pass if any attempt passes (default: 1)",
    )
    args = parser.parse_args()

    ade_bench_dir = Path(os.environ.get("ADE_BENCH_DIR", "~/repos/ade-bench")).expanduser()
    if not ade_bench_dir.exists():
        sys.exit(f"ADE_BENCH_DIR not found: {ade_bench_dir}")

    suite = AdeBenchSuite(ade_bench_dir=ade_bench_dir)

    task_ids: list[str] | None = args.tasks
    if task_ids is not None:
        all_task_ids = {t.id for t in suite.tasks()}
        missing = set(task_ids) - all_task_ids
        if missing:
            print(f"Warning: tasks not found in suite: {', '.join(sorted(missing))}")

    n_tasks = sum(1 for t in suite.tasks() if task_ids is None or t.id in set(task_ids or []))
    print(
        f"Running {n_tasks} ADE-bench task(s) with agent={args.agent}, n_attempts={args.n_attempts}"
    )

    report = asyncio.run(run(suite, task_ids, args.n_attempts))
    print(report_to_markdown(report))


if __name__ == "__main__":
    main()
