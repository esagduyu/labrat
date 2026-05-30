"""Maps a parsed ADE trial dict to TrialResult.

Ported from src/labrat/eval/runners/ade_bench_runner.py (_map_trial).
"""

from __future__ import annotations

from typing import Any

from labrat.eval.types import TrialResult

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
    "no_experiments_dir",
    "no_metadata",
    "task_not_in_metadata",
}


def trial_dict_to_result(trial: dict[str, Any], trial_num: int) -> TrialResult:
    failure_mode: str = trial.get("failure_mode", "none") or "none"
    is_resolved: bool = bool(trial.get("is_resolved"))
    runtime_ms: int = int(trial.get("runtime_ms") or 0)

    if is_resolved:
        passed = True
        reason = None
    elif failure_mode in _INFRA_FAILURE_MODES:
        passed = False
        reason = f"infra:{failure_mode}"
    else:
        passed = False
        reason = failure_mode

    return TrialResult(
        task_id=trial["task_id"],
        trial_num=trial_num,
        passed=passed,
        reason=reason,
        latency_seconds=runtime_ms / 1000.0,
        artifact={
            "type": "container_state",
            "payload": {"experiment_dir": trial.get("experiment_dir")},
        },
    )
