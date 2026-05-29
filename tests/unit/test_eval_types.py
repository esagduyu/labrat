from labrat.eval.types import (
    AggregateScore,
    BenchmarkReport,
    BenchmarkTask,
    TrialResult,
)


def test_benchmark_task_required_fields():
    task = BenchmarkTask(id="t1", benchmark="dab", prompt="What is X?")
    assert task.id == "t1"
    assert task.benchmark == "dab"
    assert task.prompt == "What is X?"
    assert task.difficulty is None
    assert task.tags == []
    assert task.config == {}


def test_benchmark_task_is_frozen():
    task = BenchmarkTask(id="t1", benchmark="dab", prompt="hi")
    import pydantic

    try:
        task.id = "t2"  # type: ignore[misc]
    except (pydantic.ValidationError, TypeError):
        return
    raise AssertionError("BenchmarkTask should be frozen")


def test_trial_result_defaults():
    r = TrialResult(task_id="t1", trial_num=0, passed=True, latency_seconds=1.5)
    assert r.tool_calls == 0
    assert r.cost_usd == 0.0
    assert r.artifact == {}
    assert r.meta == {}
    assert r.reason is None


def test_aggregate_score_carries_dimensions():
    score = AggregateScore(
        overall=0.5,
        per_task={"a": 1.0, "b": 0.0},
        by_dimension={"difficulty": {"easy": 1.0, "hard": 0.0}},
        n_tasks=2,
        n_trials=4,
        n_passes=2,
    )
    assert score.by_dimension["difficulty"]["easy"] == 1.0


def test_benchmark_report_roundtrips():
    report = BenchmarkReport(
        benchmark="dab",
        run_id="r1",
        score=AggregateScore(overall=1.0, per_task={"t1": 1.0}, n_tasks=1, n_trials=1, n_passes=1),
        trials=[TrialResult(task_id="t1", trial_num=0, passed=True, latency_seconds=1.0)],
        config={"hints": True},
    )
    dumped = report.model_dump()
    restored = BenchmarkReport.model_validate(dumped)
    assert restored == report
