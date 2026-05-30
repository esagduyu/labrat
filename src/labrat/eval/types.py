"""Unified benchmark-suite types and protocol.

See docs/superpowers/specs/2026-05-28-unified-benchmark-suite-design.md.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class BenchmarkTask(BaseModel):
    """A single benchmark task. Superset of the legacy EvalCase."""

    model_config = ConfigDict(frozen=True)

    id: str
    benchmark: str
    prompt: str
    difficulty: str | None = None
    tags: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class TrialResult(BaseModel):
    """Outcome of one trial of one task."""

    task_id: str
    trial_num: int
    passed: bool
    reason: str | None = None
    latency_seconds: float
    tool_calls: int = 0
    cost_usd: float = 0.0
    artifact: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class AggregateScore(BaseModel):
    """Aggregated score for one benchmark run."""

    overall: float
    per_task: dict[str, float]
    by_dimension: dict[str, dict[str, float]] = Field(default_factory=dict)
    n_tasks: int
    n_trials: int
    n_passes: int


class BenchmarkReport(BaseModel):
    """Full report of one benchmark run."""

    benchmark: str
    run_id: str
    score: AggregateScore
    trials: list[TrialResult]
    config: dict[str, Any]


@runtime_checkable
class BenchmarkSuite(Protocol):
    """The contract every benchmark integration implements."""

    @property
    def name(self) -> str: ...

    def tasks(self) -> Iterable[BenchmarkTask]: ...

    async def run_trial(
        self, task: BenchmarkTask, trial_num: int, scratch_dir: Path
    ) -> TrialResult: ...

    def aggregate(self, results: list[TrialResult]) -> AggregateScore: ...

    def write_submission(self, report: BenchmarkReport, output_dir: Path) -> None: ...
