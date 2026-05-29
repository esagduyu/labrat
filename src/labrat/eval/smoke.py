"""Smoke regression: SubsetSuite wrapper + ADE smoke task list.

Per docs/superpowers/specs/2026-05-28-unified-benchmark-suite-design.md.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from labrat.eval.types import (
    AggregateScore,
    BenchmarkReport,
    BenchmarkSuite,
    BenchmarkTask,
    TrialResult,
)


class SubsetSuite:
    """Wraps a BenchmarkSuite to expose only a fixed task subset.

    Delegates run_trial / aggregate to the parent. write_submission is a no-op
    (subsets never produce submissions).
    """

    def __init__(
        self,
        parent: BenchmarkSuite,
        task_ids: list[str],
        name: str | None = None,
    ) -> None:
        self._parent = parent
        self._task_ids: set[str] = set(task_ids)
        self.name: str = name or f"{parent.name}-subset"

    def tasks(self) -> Iterable[BenchmarkTask]:
        return [t for t in self._parent.tasks() if t.id in self._task_ids]

    async def run_trial(
        self, task: BenchmarkTask, trial_num: int, scratch_dir: Path
    ) -> TrialResult:
        return await self._parent.run_trial(task, trial_num, scratch_dir)

    def aggregate(self, results: list[TrialResult]) -> AggregateScore:
        return self._parent.aggregate(results)

    def write_submission(self, report: BenchmarkReport, output_dir: Path) -> None:
        return None


# Frozen smoke task set. Populated from docs/superpowers/notes/2026-05-29-ade-smoke-selection.md.
# Changing this invalidates tests/baselines/ade_smoke_baseline.json.
ADE_SMOKE_TASK_IDS: list[str] = [
    # Populated in Task 14 once AdeBenchSuite exists.
]


def ade_smoke_suite() -> BenchmarkSuite:
    """Build the ADE smoke regression suite — frozen task subset of AdeBenchSuite."""
    # Import locally to avoid a top-level cycle if AdeBenchSuite ever depends on smoke.
    # type: ignore[import-not-found] will be resolved when AdeBenchSuite lands in Task 11.
    from labrat.eval.benchmarks.ade_bench.suite import (  # type: ignore[import-not-found]
        AdeBenchSuite,  # type: ignore[no-redef]
    )

    return SubsetSuite(AdeBenchSuite(), ADE_SMOKE_TASK_IDS, name="ade-smoke")  # type: ignore[arg-type]
