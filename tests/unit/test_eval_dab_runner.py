"""Tests for eval_dab.py runner logic — resumability and n_trials semantics."""

from __future__ import annotations

from pathlib import Path

import pytest

from labrat.eval.types import BenchmarkTask, TrialResult


def _make_task(task_id: str = "ds:1") -> BenchmarkTask:
    return BenchmarkTask(
        id=task_id,
        benchmark="dab",
        prompt="How many?",
        config={
            "dataset": task_id.split(":")[0],
            "query_num": task_id.split(":")[1],
            "db_config_path": "/fake/db_config.yaml",
            "validator_path": "/fake/validate.py",
            "hints": False,
        },
    )


def _make_trial(task_id: str, trial_num: int, passed: bool) -> TrialResult:
    return TrialResult(
        task_id=task_id,
        trial_num=trial_num,
        passed=passed,
        reason=None,
        latency_seconds=1.0,
        tool_calls=0,
        artifact={"type": "text", "payload": "42"},
    )


def test_runner_skips_already_completed_trials(tmp_path: Path) -> None:
    """If trials.jsonl already contains (task_id, trial_num), that trial is not re-run."""
    from scripts.eval_dab import _load_completed_trials

    trials_jsonl = tmp_path / "trials.jsonl"
    existing = _make_trial("ds:1", trial_num=0, passed=True)
    trials_jsonl.write_text(existing.model_dump_json() + "\n")

    completed = _load_completed_trials(trials_jsonl)
    assert ("ds:1", 0) in completed
    assert ("ds:1", 1) not in completed


def test_runner_appends_new_trials_to_existing_jsonl(tmp_path: Path) -> None:
    """Re-running with a partial trials.jsonl appends only missing trials."""
    from scripts.eval_dab import _load_completed_trials

    trials_jsonl = tmp_path / "trials.jsonl"
    t0 = _make_trial("ds:1", trial_num=0, passed=True)
    trials_jsonl.write_text(t0.model_dump_json() + "\n")

    completed = _load_completed_trials(trials_jsonl)
    # trial 1 not in completed — would be run
    assert ("ds:1", 1) not in completed
    # trial 0 already there — would be skipped
    assert ("ds:1", 0) in completed


def test_n_trials_default_is_5() -> None:
    """Phase 1b default for --n-trials must be 5 (not 1 as in Phase 1a)."""
    import ast
    from pathlib import Path

    src = Path("scripts/eval_dab.py").read_text()
    tree = ast.parse(src)

    # Find the add_argument("--n-trials", ..., default=...) call
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_argument"):
            continue
        args_strs = [
            a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)
        ]
        if "--n-trials" not in args_strs:
            continue
        for kw in node.keywords:
            if kw.arg == "default" and isinstance(kw.value, ast.Constant):
                assert kw.value.value == 5, f"--n-trials default must be 5, got {kw.value.value}"
                return
    pytest.fail("--n-trials add_argument with default not found in eval_dab.py")
