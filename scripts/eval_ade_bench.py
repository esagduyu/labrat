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
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))


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

    from labrat.eval.runners.ade_bench_runner import AdeBenchRunner
    from labrat.eval.suites.ade_bench import AdeBenchSuite

    suite = AdeBenchSuite(ade_bench_dir=ade_bench_dir)
    cases = suite.cases
    if args.tasks:
        ids = set(args.tasks)
        cases = [c for c in cases if c.id in ids]
        missing = ids - {c.id for c in cases}
        if missing:
            print(f"Warning: tasks not found in suite: {', '.join(sorted(missing))}")

    print(
        f"Running {len(cases)} ADE-bench task(s) with agent={args.agent}, n_attempts={args.n_attempts}"
    )

    runner = AdeBenchRunner(
        cases=cases,
        ade_bench_dir=ade_bench_dir,
        agent=args.agent,
        output_path=args.output_dir,
        n_concurrent_trials=args.concurrent,
        n_attempts=args.n_attempts,
    )
    report = runner.run()
    print(report.to_markdown())


if __name__ == "__main__":
    main()
