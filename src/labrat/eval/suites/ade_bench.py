"""ADE-bench suite: loads tasks from the ade-bench repo."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from labrat.eval.models import EvalCase


class AdeBenchSuite:
    """EvalSuite that reads task definitions from an ADE-bench checkout.

    Requires the ADE_BENCH_DIR environment variable or an explicit path.
    Only includes tasks that have a duckdb+dbt variant and match status_filter.
    """

    suite_name = "ade-bench"

    def __init__(
        self,
        ade_bench_dir: Path | None = None,
        status_filter: str = "ready",
    ) -> None:
        self._dir = (
            ade_bench_dir or Path(os.environ.get("ADE_BENCH_DIR", "~/repos/ade-bench")).expanduser()
        )
        self._status_filter = status_filter
        self._cases: list[EvalCase] | None = None

    @property
    def cases(self) -> list[EvalCase]:
        if self._cases is None:
            self._cases = self._load()
        return self._cases

    def _load(self) -> list[EvalCase]:
        tasks_dir = self._dir / "tasks"
        if not tasks_dir.exists():
            return []

        cases: list[EvalCase] = []
        for task_yaml in sorted(tasks_dir.glob("*/task.yaml")):
            data = yaml.safe_load(task_yaml.read_text())

            if self._status_filter and data.get("status") != self._status_filter:
                continue

            variants = data.get("variants", [])
            has_duckdb_dbt = any(
                v.get("db_type") == "duckdb" and v.get("project_type", "dbt") == "dbt"
                for v in variants
            )
            if not has_duckdb_dbt:
                continue

            prompts = data.get("prompts", [])
            base_prompt = next(
                (p["prompt"] for p in prompts if p.get("key") == "base"),
                prompts[0]["prompt"] if prompts else "",
            )
            if not base_prompt:
                continue

            cases.append(
                EvalCase(
                    id=data["task_id"],
                    question=base_prompt,
                    domain=",".join(data.get("tags", [])),
                    dialect="duckdb",
                )
            )
        return cases
