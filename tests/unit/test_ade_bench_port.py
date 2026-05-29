from unittest.mock import patch

from labrat.eval.benchmarks.ade_bench.suite import AdeBenchSuite
from labrat.eval.types import BenchmarkSuite, TrialResult


def test_ade_bench_suite_implements_protocol():
    suite = AdeBenchSuite()
    assert isinstance(suite, BenchmarkSuite)
    assert suite.name == "ade-bench"


def test_ade_bench_suite_enumerates_tasks(tmp_path):
    tasks_dir = tmp_path / "tasks" / "fake_easy_01"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "task.yaml").write_text(
        """
task_id: fake_easy_01
status: ready
difficulty: easy
tags: [demo]
variants:
  - db_type: duckdb
    project_type: dbt
prompts:
  - key: base
    prompt: Do the thing.
"""
    )

    suite = AdeBenchSuite(ade_bench_dir=tmp_path)
    tasks = list(suite.tasks())
    assert len(tasks) == 1
    t = tasks[0]
    assert t.id == "fake_easy_01"
    assert t.benchmark == "ade_bench"
    assert t.difficulty == "easy"
    assert t.prompt == "Do the thing."


def test_ade_bench_suite_skips_non_ready(tmp_path):
    d = tmp_path / "tasks" / "draft_task"
    d.mkdir(parents=True)
    (d / "task.yaml").write_text(
        """
task_id: draft_task
status: draft
variants:
  - db_type: duckdb
    project_type: dbt
prompts:
  - key: base
    prompt: x
"""
    )
    suite = AdeBenchSuite(ade_bench_dir=tmp_path)
    assert list(suite.tasks()) == []


def test_ade_bench_suite_skips_non_duckdb(tmp_path):
    d = tmp_path / "tasks" / "pg_task"
    d.mkdir(parents=True)
    (d / "task.yaml").write_text(
        """
task_id: pg_task
status: ready
variants:
  - db_type: postgres
    project_type: dbt
prompts:
  - key: base
    prompt: x
"""
    )
    suite = AdeBenchSuite(ade_bench_dir=tmp_path)
    assert list(suite.tasks()) == []


async def test_ade_bench_suite_run_trial_maps_passing_attempt(tmp_path):
    tasks_dir = tmp_path / "tasks" / "fake_easy_01"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "task.yaml").write_text(
        """
task_id: fake_easy_01
status: ready
difficulty: easy
variants:
  - db_type: duckdb
    project_type: dbt
prompts:
  - key: base
    prompt: Do the thing.
"""
    )
    suite = AdeBenchSuite(ade_bench_dir=tmp_path)
    task = next(iter(suite.tasks()))

    with patch("labrat.eval.benchmarks.ade_bench.external_runner.run_one") as mock_run_one:
        mock_run_one.return_value = {
            "task_id": "fake_easy_01",
            "is_resolved": True,
            "failure_mode": "none",
            "runtime_ms": 12345,
            "experiment_dir": str(tmp_path / "experiments" / "e1"),
        }
        result = await suite.run_trial(task, 0, tmp_path / "scratch")

    assert isinstance(result, TrialResult)
    assert result.passed is True
    assert result.task_id == "fake_easy_01"
    assert result.trial_num == 0
    assert result.latency_seconds == 12345 / 1000.0


def test_ade_bench_suite_aggregate_stratifies_by_difficulty(tmp_path):
    for diff in ("easy", "medium"):
        d = tmp_path / "tasks" / f"fake_{diff}_01"
        d.mkdir(parents=True)
        (d / "task.yaml").write_text(
            f"""
task_id: fake_{diff}_01
status: ready
difficulty: {diff}
variants:
  - db_type: duckdb
    project_type: dbt
prompts:
  - key: base
    prompt: x
"""
        )
    suite = AdeBenchSuite(ade_bench_dir=tmp_path)
    list(suite.tasks())  # populate cache
    results = [
        TrialResult(task_id="fake_easy_01", trial_num=0, passed=True, latency_seconds=1.0),
        TrialResult(task_id="fake_medium_01", trial_num=0, passed=False, latency_seconds=1.0),
    ]
    score = suite.aggregate(results)
    assert score.by_dimension["difficulty"]["easy"] == 1.0
    assert score.by_dimension["difficulty"]["medium"] == 0.0
    assert score.overall == 0.5


def test_ade_bench_suite_accepts_missing_project_type(tmp_path):
    """A variant with no project_type defaults to dbt (legacy behavior)."""
    d = tmp_path / "tasks" / "fake_easy_01"
    d.mkdir(parents=True)
    (d / "task.yaml").write_text(
        """
task_id: fake_easy_01
status: ready
difficulty: easy
variants:
  - db_type: duckdb
prompts:
  - key: base
    prompt: x
"""
    )
    suite = AdeBenchSuite(ade_bench_dir=tmp_path)
    tasks = list(suite.tasks())
    assert len(tasks) == 1
    assert tasks[0].id == "fake_easy_01"
