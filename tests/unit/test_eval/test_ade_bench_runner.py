"""Tests for AdeBenchRunner n_attempts and multi-trial result grouping."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from labrat.eval.models import EvalStatus
from labrat.eval.runners.ade_bench_runner import AdeBenchRunner


def _make_case(task_id: str):
    from labrat.eval.models import EvalCase

    return EvalCase(id=task_id, question=f"Question for {task_id}")


def _make_results_json(trials: list[dict]) -> Path:
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, dir="/tmp")
    json.dump({"results": trials}, tmp)
    tmp.flush()
    return Path(tmp.name)


def _trial(task_id: str, is_resolved: bool, attempt: int = 1, n_attempts: int = 1) -> dict:
    return {
        "trial_name": f"{task_id}.base.{attempt}-of-{n_attempts}",
        "task_id": task_id,
        "task_prompt": "...",
        "is_resolved": is_resolved,
        "failure_mode": "none" if is_resolved else "unset",
        "runtime_ms": 1000,
    }


# ── n_attempts CLI flag ────────────────────────────────────────────────────────


def test_n_attempts_included_in_ade_command():
    """--n-attempts N is passed to the ade run command."""
    ade_bench_dir = Path("/tmp/fake-ade-bench")
    runner = AdeBenchRunner(
        cases=[_make_case("airbnb001")],
        ade_bench_dir=ade_bench_dir,
        n_attempts=3,
    )

    with (
        patch("subprocess.run") as mock_run,
        patch.object(runner, "_find_results_json", return_value=None),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner.run()

    cmd = mock_run.call_args[0][0]
    assert "--n-attempts" in cmd
    assert "3" in cmd


def test_n_attempts_default_is_one():
    """Without specifying n_attempts, --n-attempts 1 is sent."""
    ade_bench_dir = Path("/tmp/fake-ade-bench")
    runner = AdeBenchRunner(
        cases=[_make_case("airbnb001")],
        ade_bench_dir=ade_bench_dir,
    )

    with (
        patch("subprocess.run") as mock_run,
        patch.object(runner, "_find_results_json", return_value=None),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner.run()

    cmd = mock_run.call_args[0][0]
    assert "--n-attempts" in cmd
    idx = cmd.index("--n-attempts")
    assert cmd[idx + 1] == "1"


# ── multi-trial best-of grouping ──────────────────────────────────────────────


def test_pass_if_any_trial_passes():
    """With 3 attempts, task is marked correct if any attempt passes."""
    results_path = _make_results_json(
        [
            _trial("airbnb001", is_resolved=False, attempt=1, n_attempts=3),
            _trial("airbnb001", is_resolved=False, attempt=2, n_attempts=3),
            _trial("airbnb001", is_resolved=True, attempt=3, n_attempts=3),
        ]
    )
    ade_bench_dir = results_path.parent
    runner = AdeBenchRunner(
        cases=[_make_case("airbnb001")],
        ade_bench_dir=ade_bench_dir,
        n_attempts=3,
    )

    with (
        patch("subprocess.run") as mock_run,
        patch.object(runner, "_find_results_json", return_value=results_path),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        report = runner.run()

    assert report.results[0].status == EvalStatus.correct


def test_fail_if_all_trials_fail():
    """Task is wrong if all 3 attempts fail."""
    results_path = _make_results_json(
        [
            _trial("airbnb001", is_resolved=False, attempt=1, n_attempts=3),
            _trial("airbnb001", is_resolved=False, attempt=2, n_attempts=3),
            _trial("airbnb001", is_resolved=False, attempt=3, n_attempts=3),
        ]
    )
    ade_bench_dir = results_path.parent
    runner = AdeBenchRunner(
        cases=[_make_case("airbnb001")],
        ade_bench_dir=ade_bench_dir,
        n_attempts=3,
    )

    with (
        patch("subprocess.run") as mock_run,
        patch.object(runner, "_find_results_json", return_value=results_path),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        report = runner.run()

    assert report.results[0].status == EvalStatus.wrong


def test_single_trial_still_works():
    """Default n_attempts=1 single-trial behavior is unchanged."""
    results_path = _make_results_json(
        [
            _trial("airbnb001", is_resolved=True, attempt=1, n_attempts=1),
        ]
    )
    ade_bench_dir = results_path.parent
    runner = AdeBenchRunner(
        cases=[_make_case("airbnb001")],
        ade_bench_dir=ade_bench_dir,
    )

    with (
        patch("subprocess.run") as mock_run,
        patch.object(runner, "_find_results_json", return_value=results_path),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        report = runner.run()

    assert report.results[0].status == EvalStatus.correct
