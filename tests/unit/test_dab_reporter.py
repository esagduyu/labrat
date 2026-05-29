import json
from pathlib import Path

from labrat.eval.benchmarks.dab.reporter import build_submission, write_submission_json
from labrat.eval.types import AggregateScore, BenchmarkReport, TrialResult


def _report(trials: list[TrialResult]) -> BenchmarkReport:
    return BenchmarkReport(
        benchmark="dab",
        run_id="r1",
        score=AggregateScore(
            overall=0.5,
            per_task={t.task_id: 1.0 if t.passed else 0.0 for t in trials},
            n_tasks=len({t.task_id for t in trials}),
            n_trials=len(trials),
            n_passes=sum(1 for t in trials if t.passed),
        ),
        trials=trials,
        config={},
    )


def test_build_submission_shape() -> None:
    trials = [
        TrialResult(
            task_id="bookreview:1",
            trial_num=0,
            passed=True,
            latency_seconds=0.0,
            artifact={"type": "text", "payload": "answer A"},
        ),
        TrialResult(
            task_id="bookreview:1",
            trial_num=1,
            passed=False,
            latency_seconds=0.0,
            artifact={"type": "text", "payload": "answer B"},
        ),
    ]
    entries = build_submission(trials)
    assert len(entries) == 2
    assert entries[0] == {"dataset": "bookreview", "query": "1", "run": 0, "answer": "answer A"}
    assert entries[1] == {"dataset": "bookreview", "query": "1", "run": 1, "answer": "answer B"}


def test_write_submission_json_creates_file(tmp_path: Path) -> None:
    trials = [
        TrialResult(
            task_id="yelp:3",
            trial_num=0,
            passed=True,
            latency_seconds=0.0,
            artifact={"type": "text", "payload": "x"},
        ),
    ]
    write_submission_json(_report(trials), tmp_path)
    output = tmp_path / "submission.json"
    assert output.exists()
    data = json.loads(output.read_text())
    assert data == [{"dataset": "yelp", "query": "3", "run": 0, "answer": "x"}]
