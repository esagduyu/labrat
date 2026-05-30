from collections.abc import Iterable
from pathlib import Path

from labrat.eval.smoke import SubsetSuite
from labrat.eval.types import AggregateScore, BenchmarkReport, BenchmarkTask, TrialResult


class _FakeSuite:
    name = "fake"

    def __init__(self):
        self._tasks = [
            BenchmarkTask(id="a", benchmark="fake", prompt="?"),
            BenchmarkTask(id="b", benchmark="fake", prompt="?"),
            BenchmarkTask(id="c", benchmark="fake", prompt="?"),
        ]
        self.run_trial_calls: list[tuple[str, int]] = []

    def tasks(self) -> Iterable[BenchmarkTask]:
        return self._tasks

    async def run_trial(
        self, task: BenchmarkTask, trial_num: int, scratch_dir: Path
    ) -> TrialResult:
        self.run_trial_calls.append((task.id, trial_num))
        return TrialResult(task_id=task.id, trial_num=trial_num, passed=True, latency_seconds=0.0)

    def aggregate(self, results: list[TrialResult]) -> AggregateScore:
        return AggregateScore(
            overall=1.0,
            per_task={r.task_id: 1.0 for r in results},
            n_tasks=len(results),
            n_trials=len(results),
            n_passes=sum(1 for r in results if r.passed),
        )

    def write_submission(self, report: BenchmarkReport, output_dir: Path) -> None:
        pass


def test_subset_suite_filters_tasks():
    parent = _FakeSuite()
    sub = SubsetSuite(parent, ["a", "c"], name="fake-sub")
    task_ids = sorted(t.id for t in sub.tasks())
    assert task_ids == ["a", "c"]


def test_subset_suite_uses_provided_name():
    parent = _FakeSuite()
    assert SubsetSuite(parent, ["a"], name="explicit").name == "explicit"


def test_subset_suite_default_name_extends_parent():
    parent = _FakeSuite()
    assert SubsetSuite(parent, ["a"]).name == "fake-subset"


async def test_subset_suite_delegates_run_trial(tmp_path):
    parent = _FakeSuite()
    sub = SubsetSuite(parent, ["b"])
    task = next(t for t in sub.tasks() if t.id == "b")
    result = await sub.run_trial(task, 0, tmp_path)
    assert result.task_id == "b"
    assert parent.run_trial_calls == [("b", 0)]


def test_subset_suite_write_submission_is_noop(tmp_path):
    parent = _FakeSuite()
    sub = SubsetSuite(parent, ["a"])
    report = BenchmarkReport(
        benchmark="fake",
        run_id="r",
        score=AggregateScore(overall=1.0, per_task={}, n_tasks=0, n_trials=0, n_passes=0),
        trials=[],
        config={},
    )
    sub.write_submission(report, tmp_path)
    assert list(tmp_path.iterdir()) == []
