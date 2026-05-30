"""DAB benchmark entrypoint.

Phase 1a: single-trial-per-query, no resumability, no pass@5.
Phase 1b: pass@5 (n_trials=5), JSONL resumability — re-run skips completed trials.
Phase 4 extracts the inline runner into BenchmarkOrchestrator.

Usage:
  uv run python scripts/eval_dab.py --datasets deps_dev_v1,github_repos
  uv run python scripts/eval_dab.py --tasks "deps_dev_v1:1,github_repos:2"
  uv run python scripts/eval_dab.py --hints
  # Resume a crashed run by pointing at the same output dir:
  uv run python scripts/eval_dab.py --output-dir runs/dab/dab-<id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from labrat.eval.benchmarks.dab.suite import DabSuite
from labrat.eval.reporting import report_to_markdown
from labrat.eval.types import BenchmarkReport, BenchmarkSuite, TrialResult


def _load_completed_trials(trials_jsonl: Path) -> set[tuple[str, int]]:
    """Return set of (task_id, trial_num) pairs already recorded in trials.jsonl."""
    completed: set[tuple[str, int]] = set()
    if not trials_jsonl.exists():
        return completed
    for line in trials_jsonl.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            completed.add((obj["task_id"], obj["trial_num"]))
        except (json.JSONDecodeError, KeyError):
            pass
    return completed


async def _run_interim(
    suite: BenchmarkSuite,
    n_trials: int,
    output_dir: Path,
    task_filter: list[str] | None,
) -> BenchmarkReport:
    """Inline interim runner. No DAB-specific logic. Replaced by BenchmarkOrchestrator in Phase 4."""
    output_dir.mkdir(parents=True, exist_ok=True)
    trials_jsonl = output_dir / "trials.jsonl"

    # Load any previously completed trials (resumability).
    completed = _load_completed_trials(trials_jsonl)
    all_trials: list[TrialResult] = []
    if completed:
        for line in trials_jsonl.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    all_trials.append(TrialResult.model_validate_json(line))
                except Exception:
                    pass
        print(f"Resuming: {len(completed)} trials already complete, skipping.", flush=True)

    tasks = list(suite.tasks())
    if task_filter:
        wanted = set(task_filter)
        tasks = [t for t in tasks if t.id in wanted]

    with trials_jsonl.open("a") as f:
        for task in tasks:
            for trial_num in range(n_trials):
                if (task.id, trial_num) in completed:
                    continue
                scratch = output_dir / "scratch" / f"{task.id.replace(':', '_')}__trial{trial_num}"
                scratch.mkdir(parents=True, exist_ok=True)
                result = await suite.run_trial(task, trial_num, scratch)
                f.write(result.model_dump_json() + "\n")
                f.flush()
                all_trials.append(result)
                print(
                    f"[{task.id} trial {trial_num}] "
                    f"{'PASS' if result.passed else 'FAIL'} "
                    f"({result.latency_seconds:.1f}s)",
                    flush=True,
                )

    score = suite.aggregate(all_trials)
    return BenchmarkReport(
        benchmark=suite.name,
        run_id=output_dir.name,
        score=score,
        trials=all_trials,
        config={"n_trials": n_trials, "task_filter": task_filter},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run DAB benchmark")
    parser.add_argument("--dab-dir", type=Path, default=None)
    parser.add_argument("--hints", action="store_true")
    parser.add_argument(
        "--n-trials",
        type=int,
        default=5,
        help="Trials per query for pass@k scoring (default 5)",
    )
    parser.add_argument(
        "--datasets",
        type=str,
        default=None,
        help="Comma-separated dataset filter, e.g. 'bookreview,yelp'",
    )
    parser.add_argument(
        "--tasks",
        type=str,
        default=None,
        help="Comma-separated task ID filter, e.g. 'bookreview:1,yelp:3'",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Resume a previous run by pointing at its output dir",
    )
    args = parser.parse_args(argv)

    suite = DabSuite(dab_dir=args.dab_dir, hints=args.hints)

    task_filter: list[str] | None = None
    if args.tasks:
        task_filter = [t.strip() for t in args.tasks.split(",") if t.strip()]
    elif args.datasets:
        wanted = {ds.strip() for ds in args.datasets.split(",") if ds.strip()}
        task_filter = [t.id for t in suite.tasks() if t.config["dataset"] in wanted]

    if args.output_dir:
        output_dir = args.output_dir
        # Read task_filter and hints from existing config if not overridden.
        existing_cfg = output_dir / "config.json"
        if existing_cfg.exists() and not args.tasks and not args.datasets:
            cfg = json.loads(existing_cfg.read_text())
            task_filter = cfg.get("task_filter")
    else:
        run_id = f"dab-{int(time.time())}"
        output_dir = Path("runs") / "dab" / run_id

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "config.json").write_text(
        json.dumps(
            {"hints": args.hints, "n_trials": args.n_trials, "task_filter": task_filter},
            indent=2,
        )
    )

    report = asyncio.run(_run_interim(suite, args.n_trials, output_dir, task_filter))

    suite.write_submission(report, output_dir)
    (output_dir / "report.md").write_text(report_to_markdown(report))
    print(f"\nRun complete: {output_dir}")
    print(f"Overall score: {report.score.overall:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
