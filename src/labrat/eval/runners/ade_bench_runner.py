"""AdeBenchRunner: shells out to the ade CLI and maps results to EvalReport."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from labrat.eval.models import EvalCase, EvalResult, EvalStatus
from labrat.eval.report import EvalReport

# Failure modes that indicate infrastructure problems, not wrong answers
_INFRA_FAILURE_MODES = {
    "setup_failed",
    "setup_timeout",
    "agent_setup_timeout",
    "agent_timeout",
    "test_timeout",
    "unknown_agent_error",
    "unknown_harness_error",
    "harness_panic",
    "parse_error",
    "fatal_llm_parse_error",
    "context_length_exceeded",
    "quota_exceeded",
}


def _map_trial(trial: dict[str, Any]) -> EvalResult:
    failure_mode: str = trial.get("failure_mode", "none")
    is_resolved: bool | None = trial.get("is_resolved")
    runtime_ms: int = trial.get("runtime_ms") or 0

    if is_resolved is True:
        status = EvalStatus.correct
    elif failure_mode in _INFRA_FAILURE_MODES:
        status = EvalStatus.error
    else:
        status = EvalStatus.wrong

    return EvalResult(
        case_id=trial["task_id"],
        status=status,
        error_message=failure_mode if status == EvalStatus.error else None,
        latency_seconds=runtime_ms / 1000.0,
    )


def _best_trial(trials: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the first passing trial; fall back to the last trial."""
    for t in trials:
        if t.get("is_resolved"):
            return t
    return trials[-1]


class AdeBenchRunner:
    """Runs ADE-bench tasks via the ade CLI and returns an EvalReport.

    Uses `ade run` with the sage agent by default (no LLM cost).
    Requires Docker to be running and ADE_BENCH_DIR to be set up.
    With n_attempts > 1, a task passes if any attempt passes (pass@k semantics).
    """

    def __init__(
        self,
        cases: list[EvalCase],
        ade_bench_dir: Path,
        agent: str = "sage",
        output_path: Path | None = None,
        n_concurrent_trials: int = 1,
        n_attempts: int = 1,
        no_diffs: bool = True,
    ) -> None:
        self._cases = cases
        self._ade_bench_dir = ade_bench_dir
        self._agent = agent
        self._output_path = output_path or (ade_bench_dir / "experiments")
        self._n_concurrent_trials = n_concurrent_trials
        self._n_attempts = n_attempts
        self._no_diffs = no_diffs

    def run(self) -> EvalReport:
        if not self._cases:
            return EvalReport(suite_name="ade-bench", results=[])

        task_ids = [c.id for c in self._cases]
        cmd = [
            "ade",
            "run",
            *task_ids,
            "--db",
            "duckdb",
            "--project-type",
            "dbt",
            "--agent",
            self._agent,
            "--n-concurrent-trials",
            str(self._n_concurrent_trials),
            "--n-attempts",
            str(self._n_attempts),
            "--output-path",
            str(self._output_path),
        ]
        if self._no_diffs:
            cmd.append("--no-diffs")

        proc = subprocess.run(
            cmd,
            cwd=str(self._ade_bench_dir),
            capture_output=True,
            text=True,
        )

        results_file = self._find_results_json()
        if results_file is None:
            results = [
                EvalResult(
                    case_id=c.id,
                    status=EvalStatus.error,
                    error_message=(f"ade run failed (rc={proc.returncode}): {proc.stderr[:300]}"),
                )
                for c in self._cases
            ]
            return EvalReport(suite_name="ade-bench", results=results)

        data: dict[str, Any] = json.loads(results_file.read_text())

        # Group all trials by task_id; with n_attempts > 1, pick best (pass if any passes)
        trial_groups: dict[str, list[dict[str, Any]]] = {}
        for trial in data.get("results", []):
            trial_groups.setdefault(trial["task_id"], []).append(trial)

        results: list[EvalResult] = []
        for case in self._cases:
            trials = trial_groups.get(case.id)
            if not trials:
                results.append(
                    EvalResult(
                        case_id=case.id,
                        status=EvalStatus.error,
                        error_message="task not found in results.json",
                    )
                )
            else:
                results.append(_map_trial(_best_trial(trials)))
        return EvalReport(suite_name="ade-bench", results=results)

    def _find_results_json(self) -> Path | None:
        """Find the run-level results.json at output_path/{run_id}/results.json."""
        if not self._output_path.exists():
            return None
        # Run-level file is exactly two levels deep: output_path/{run_id}/results.json
        candidates = [
            p
            for p in self._output_path.rglob("results.json")
            if p.parent.parent == self._output_path
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.stat().st_mtime)
