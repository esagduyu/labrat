from labrat.eval.reporting import report_to_markdown
from labrat.eval.types import AggregateScore, BenchmarkReport, TrialResult


def test_report_to_markdown_includes_score_and_failures():
    report = BenchmarkReport(
        benchmark="dab",
        run_id="r1",
        score=AggregateScore(
            overall=0.5,
            per_task={"t1": 1.0, "t2": 0.0},
            by_dimension={"dataset": {"agnews": 0.0, "yelp": 1.0}},
            n_tasks=2,
            n_trials=2,
            n_passes=1,
        ),
        trials=[
            TrialResult(task_id="t1", trial_num=0, passed=True, latency_seconds=1.0),
            TrialResult(
                task_id="t2",
                trial_num=0,
                passed=False,
                reason="validator_no_match",
                latency_seconds=2.0,
            ),
        ],
        config={"hints": True},
    )
    md = report_to_markdown(report)
    assert "# dab" in md
    assert "Overall: 0.50" in md or "Overall: 50" in md
    assert "agnews" in md
    assert "yelp" in md
    assert "validator_no_match" in md
