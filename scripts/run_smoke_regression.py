"""Run ADE smoke regression and compare against baseline.

Usage:
  uv run python scripts/run_smoke_regression.py capture --n-runs 3 --n-attempts 3
  uv run python scripts/run_smoke_regression.py check --n-attempts 3
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from labrat.eval.smoke import ade_smoke_suite

BASELINE_PATH = Path("tests/baselines/ade_smoke_baseline.json")

# Regression thresholds — from spec.
# Hard fail: any task previously at >=7/9 drops below 4/9.
# Soft signal: aggregate resolved-task count drops by 1.
_HARD_FAIL_BASELINE_PASSES = 7
_HARD_FAIL_CURRENT_THRESHOLD = 4 / 9


@dataclass
class RegressionVerdict:
    kind: Literal["pass", "soft_signal", "hard_fail"]
    message: str

    @property
    def exit_code(self) -> int:
        return 1 if self.kind == "hard_fail" else 0


def compare_against_baseline(
    baseline: dict[str, dict[str, int]],
    current: dict[str, float],
) -> RegressionVerdict:
    """baseline maps task_id → {passes, attempts}; current maps task_id → pass-rate."""
    hard_fail_tasks: list[str] = []
    for tid, base in baseline.items():
        if tid not in current:
            continue
        if (
            base["passes"] >= _HARD_FAIL_BASELINE_PASSES
            and current[tid] < _HARD_FAIL_CURRENT_THRESHOLD
        ):
            hard_fail_tasks.append(tid)

    if hard_fail_tasks:
        return RegressionVerdict(
            kind="hard_fail",
            message=f"Hard fail on tasks: {hard_fail_tasks}",
        )

    baseline_resolved = sum(
        1 for tid, base in baseline.items() if base["passes"] / base["attempts"] >= 0.5
    )
    current_resolved = sum(1 for tid, pr in current.items() if pr >= 0.5)
    if current_resolved < baseline_resolved:
        drop = baseline_resolved - current_resolved
        return RegressionVerdict(
            kind="soft_signal",
            message=f"Resolved-task count dropped by {drop} ({baseline_resolved} → {current_resolved})",
        )

    return RegressionVerdict(kind="pass", message="All tasks within envelope")


async def _run_smoke(n_attempts: int) -> dict[str, float]:
    """Run the smoke suite once at n_attempts per task; return per-task pass-rate."""
    suite = ade_smoke_suite()
    tasks = list(suite.tasks())
    per_task_passes: dict[str, int] = {t.id: 0 for t in tasks}
    for task in tasks:
        for attempt in range(n_attempts):
            scratch = Path("runs") / "ade_smoke" / "scratch" / f"{task.id}__attempt{attempt}"
            scratch.mkdir(parents=True, exist_ok=True)
            r = await suite.run_trial(task, attempt, scratch)
            if r.passed:
                per_task_passes[task.id] += 1
                break  # best-of-k early exit
    return {tid: per_task_passes[tid] / n_attempts for tid in per_task_passes}


def _capture(args: argparse.Namespace) -> int:
    """Capture baseline: run smoke n_runs times x n_attempts each."""
    n_runs: int = args.n_runs
    n_attempts: int = args.n_attempts
    aggregate: dict[str, int] = {}

    for run_i in range(n_runs):
        print(f"=== Smoke run {run_i + 1}/{n_runs} ===", flush=True)
        per_task = asyncio.run(_run_smoke(n_attempts))
        for tid, pr in per_task.items():
            passes = round(pr * n_attempts)
            aggregate[tid] = aggregate.get(tid, 0) + passes

    baseline = {
        tid: {"passes": passes, "attempts": n_runs * n_attempts}
        for tid, passes in aggregate.items()
    }
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps(baseline, indent=2, sort_keys=True))
    print(f"Baseline written to {BASELINE_PATH}")
    return 0


def _check(args: argparse.Namespace) -> int:
    """Run smoke once and compare against baseline."""
    if not BASELINE_PATH.exists():
        print(f"No baseline at {BASELINE_PATH} — run --capture first", file=sys.stderr)
        return 2

    baseline = json.loads(BASELINE_PATH.read_text())
    current = asyncio.run(_run_smoke(args.n_attempts))
    verdict = compare_against_baseline(baseline, current)
    print(f"Verdict: {verdict.kind} — {verdict.message}")
    return verdict.exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ADE smoke regression check")
    sub = parser.add_subparsers(dest="mode", required=True)

    cap = sub.add_parser("capture")
    cap.add_argument("--n-runs", type=int, default=3)
    cap.add_argument("--n-attempts", type=int, default=3)
    cap.set_defaults(func=_capture)

    chk = sub.add_parser("check")
    chk.add_argument("--n-attempts", type=int, default=3)
    chk.set_defaults(func=_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
