from labrat.eval.benchmarks.dab.suite import DabSuite
from labrat.eval.types import TrialResult


def _r(task_id: str, passed: bool) -> TrialResult:
    return TrialResult(task_id=task_id, trial_num=0, passed=passed, latency_seconds=0.0)


def test_aggregate_uses_stratified_dataset_weighting() -> None:
    suite = DabSuite(dab_dir=__import__("pathlib").Path("/nonexistent"))
    # Dataset a: 4 pass; dataset b: 0 pass.
    # Per-query mean = 4/5 = 0.8, but stratified = (1.0 + 0.0) / 2 = 0.5.
    results = [
        _r("a:1", True),
        _r("a:2", True),
        _r("a:3", True),
        _r("a:4", True),
        _r("b:1", False),
    ]
    score = suite.aggregate(results)
    assert score.overall == 0.5
    assert score.by_dimension["dataset"]["a"] == 1.0
    assert score.by_dimension["dataset"]["b"] == 0.0


def test_aggregate_accumulates_trials_per_query() -> None:
    suite = DabSuite(dab_dir=__import__("pathlib").Path("/nonexistent"))
    results = [_r("a:1", True), _r("a:1", False), _r("a:1", True)]
    score = suite.aggregate(results)
    assert abs(score.per_task["a:1"] - 2 / 3) < 1e-9


def test_aggregate_empty_returns_zero() -> None:
    suite = DabSuite(dab_dir=__import__("pathlib").Path("/nonexistent"))
    score = suite.aggregate([])
    assert score.overall == 0.0
    assert score.n_tasks == 0
