"""Acceptance test: legacy AdeBenchSuite/Runner vs new benchmarks/ade_bench produce
identical per-trial results for one easy task.

Exit 0 on match, 1 on mismatch.

IMPORTANT: AdeBenchRunner.run() is synchronous (subprocess.run under the hood), so
we call it directly — no await.

KNOWN LEGACY BUG: AdeBenchRunner invokes "ade" as a bare command, but in this
environment "ade" is not on PATH — it must be called as "uv run ade".  The new
external_runner.py already fixes this.  To compare apples-to-apples we replicate the
legacy runner's logic here but substitute "uv run ade" for "ade".  Everything else
(result parsing, pass/fail mapping via _map_trial/_best_trial) is exercised verbatim
through the legacy module.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from labrat.eval.benchmarks.ade_bench.suite import AdeBenchSuite as NewSuite
from labrat.eval.models import EvalStatus
from labrat.eval.runners.ade_bench_runner import (  # type: ignore[attr-defined]
    _best_trial,
    _map_trial,
)
from labrat.eval.suites.ade_bench import AdeBenchSuite as LegacySuite

TEST_TASK_ID = "analytics_engineering001"


def _run_legacy_via_uv(
    task_id: str, ade_bench_dir: Path, output_path: Path, agent: str
) -> dict[str, Any]:
    """Run ade via 'uv run ade' (same logic as AdeBenchRunner.run, fixing PATH issue).

    Returns the parsed trial dict from results.json, same structure that
    _map_trial/_best_trial expect.
    """
    cmd = [
        "uv",
        "run",
        "ade",
        "run",
        task_id,
        "--db",
        "duckdb",
        "--project-type",
        "dbt",
        "--agent",
        agent,
        "--n-concurrent-trials",
        "1",
        "--n-attempts",
        "1",
        "--output-path",
        str(output_path),
        "--no-diffs",
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(ade_bench_dir),
        capture_output=True,
        text=True,
    )

    # Find the most-recently-modified results.json under output_path (legacy logic)
    candidates = [p for p in output_path.rglob("results.json") if p.parent.parent == output_path]
    if not candidates:
        return {
            "task_id": task_id,
            "is_resolved": False,
            "failure_mode": f"ade run failed (rc={proc.returncode}): {proc.stderr[:300]}",
            "runtime_ms": 0,
        }

    results_file = max(candidates, key=lambda p: p.stat().st_mtime)
    data: dict[str, Any] = json.loads(results_file.read_text())
    trials: list[dict[str, Any]] = data.get("results", [])
    task_trials = [t for t in trials if t.get("task_id") == task_id]
    if not task_trials:
        return {
            "task_id": task_id,
            "is_resolved": False,
            "failure_mode": "task not found in results.json",
            "runtime_ms": 0,
        }
    return _best_trial(task_trials)


async def main() -> int:
    ade_bench_dir = Path(os.environ.get("ADE_BENCH_DIR", "~/repos/ade-bench")).expanduser()

    print(f"ADE_BENCH_DIR: {ade_bench_dir}")
    print(f"Task: {TEST_TASK_ID}")
    print()

    # ------------------------------------------------------------------
    # Legacy path
    # ------------------------------------------------------------------
    print("=== LEGACY PATH ===")
    legacy_suite = LegacySuite(ade_bench_dir=ade_bench_dir)
    legacy_cases = [c for c in legacy_suite.cases if c.id == TEST_TASK_ID]
    if not legacy_cases:
        print(f"Task {TEST_TASK_ID} not found in legacy suite", file=sys.stderr)
        return 1

    # Isolated output dir so legacy and new don't cross-contaminate
    with tempfile.TemporaryDirectory(prefix="ade_acceptance_legacy_") as legacy_tmp:
        legacy_trial_dict = _run_legacy_via_uv(
            task_id=TEST_TASK_ID,
            ade_bench_dir=ade_bench_dir,
            output_path=Path(legacy_tmp),
            agent="labrat_local",
        )

    # Map via legacy module's own _map_trial so we exercise the legacy code path
    legacy_result = _map_trial(legacy_trial_dict)
    legacy_pass = legacy_result.status == EvalStatus.correct
    print(
        f"Legacy: status={legacy_result.status}  pass={legacy_pass}"
        f"  latency={legacy_result.latency_seconds:.2f}s"
    )

    # ------------------------------------------------------------------
    # New path
    # ------------------------------------------------------------------
    print()
    print("=== NEW PATH ===")
    new_suite = NewSuite(ade_bench_dir=ade_bench_dir)
    new_tasks = [t for t in new_suite.tasks() if t.id == TEST_TASK_ID]
    if not new_tasks:
        print(f"Task {TEST_TASK_ID} not found in new suite", file=sys.stderr)
        return 1

    scratch = ade_bench_dir / "experiments" / "_port_acceptance_scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    new_result = await new_suite.run_trial(new_tasks[0], trial_num=0, scratch_dir=scratch)
    new_pass = new_result.passed
    print(
        f"New:    passed={new_pass}"
        f"  reason={new_result.reason}"
        f"  latency={new_result.latency_seconds:.2f}s"
    )

    # ------------------------------------------------------------------
    # Verdict
    # ------------------------------------------------------------------
    print()
    if legacy_pass != new_pass:
        print(
            f"MISMATCH: legacy pass={legacy_pass}  new pass={new_pass}",
            file=sys.stderr,
        )
        print(f"  legacy status={legacy_result.status!r}  error={legacy_result.error_message!r}")
        print(f"  new reason={new_result.reason!r}  artifact={new_result.artifact}")
        return 1

    print("ACCEPTANCE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
