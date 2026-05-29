"""ADE-bench BenchmarkSuite — port of legacy eval/suites/ade_bench.py."""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any, cast

import yaml

from labrat.eval.benchmarks.ade_bench import external_runner, reporter
from labrat.eval.types import (
    AggregateScore,
    BenchmarkReport,
    BenchmarkTask,
    TrialResult,
)


class AdeBenchSuite:
    """Reads task definitions from an ADE-bench checkout and shells to the ade CLI."""

    def __init__(
        self,
        ade_bench_dir: Path | None = None,
        status_filter: str = "ready",
    ) -> None:
        self._dir = (
            ade_bench_dir or Path(os.environ.get("ADE_BENCH_DIR", "~/repos/ade-bench")).expanduser()
        )
        self._status_filter = status_filter
        self._tasks_cache: list[BenchmarkTask] | None = None

    @property
    def name(self) -> str:
        return "ade-bench"

    def tasks(self) -> Iterable[BenchmarkTask]:
        if self._tasks_cache is None:
            self._tasks_cache = self._load_tasks()
        return self._tasks_cache

    def _load_tasks(self) -> list[BenchmarkTask]:
        tasks_dir = self._dir / "tasks"
        if not tasks_dir.exists():
            return []

        result: list[BenchmarkTask] = []
        for task_yaml in sorted(tasks_dir.glob("*/task.yaml")):
            raw: Any = yaml.safe_load(task_yaml.read_text())
            if not isinstance(raw, dict):
                continue
            data: dict[str, Any] = cast(dict[str, Any], raw)

            if self._status_filter and data.get("status") != self._status_filter:
                continue

            variants: list[Any] = data.get("variants") or []
            if not any(
                v.get("db_type") == "duckdb" and v.get("project_type", "dbt") == "dbt"
                for v in variants
            ):
                continue

            prompts: list[Any] = data.get("prompts") or []
            base_prompt: str = next(
                (str(p["prompt"]) for p in prompts if p.get("key") == "base"),
                str(prompts[0]["prompt"]) if prompts else "",
            )
            if not base_prompt:
                continue

            task_id: str = str(data["task_id"])
            difficulty_raw = data.get("difficulty")
            difficulty: str | None = str(difficulty_raw) if difficulty_raw is not None else None
            tags_raw: list[Any] = list(data.get("tags") or [])
            tags: list[str] = [str(t) for t in tags_raw]

            result.append(
                BenchmarkTask(
                    id=task_id,
                    benchmark="ade_bench",
                    prompt=base_prompt,
                    difficulty=difficulty,
                    tags=tags,
                    config={"variant": "duckdb_dbt"},
                )
            )
        return result

    async def run_trial(
        self, task: BenchmarkTask, trial_num: int, scratch_dir: Path
    ) -> TrialResult:
        trial_dict = external_runner.run_one(task.id, ade_bench_dir=self._dir)
        return reporter.trial_dict_to_result(trial_dict, trial_num=trial_num)

    def aggregate(self, results: list[TrialResult]) -> AggregateScore:
        if not results:
            return AggregateScore(overall=0.0, per_task={}, n_tasks=0, n_trials=0, n_passes=0)

        per_task: dict[str, list[bool]] = {}
        for r in results:
            per_task.setdefault(r.task_id, []).append(r.passed)
        per_task_pass_rate = {tid: sum(passes) / len(passes) for tid, passes in per_task.items()}

        difficulty_by_id = {t.id: t.difficulty for t in self.tasks()}
        by_difficulty: dict[str, list[float]] = {}
        for tid, pr in per_task_pass_rate.items():
            diff = difficulty_by_id.get(tid) or "unknown"
            by_difficulty.setdefault(diff, []).append(pr)

        return AggregateScore(
            overall=sum(per_task_pass_rate.values()) / len(per_task_pass_rate),
            per_task=per_task_pass_rate,
            by_dimension={
                "difficulty": {diff: sum(vs) / len(vs) for diff, vs in by_difficulty.items()}
            },
            n_tasks=len(per_task),
            n_trials=len(results),
            n_passes=sum(1 for r in results if r.passed),
        )

    def write_submission(self, report: BenchmarkReport, output_dir: Path) -> None:
        return None
