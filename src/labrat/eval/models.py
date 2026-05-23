"""Core eval models (M26)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class EvalStatus(StrEnum):
    correct = "correct"
    wrong = "wrong"
    error = "error"
    skipped = "skipped"


class EvalCase(BaseModel):
    """A single evaluation case."""

    model_config = ConfigDict(frozen=True)

    id: str
    question: str
    expected_sql: str | None = None
    gold_result: str | None = None
    domain: str | None = None
    dialect: str | None = None
    rubric: str | None = None


class EvalResult(BaseModel):
    """Result of running a single EvalCase."""

    model_config = ConfigDict(frozen=True)

    case_id: str
    status: EvalStatus
    generated_sql: str | None = None
    error_message: str | None = None
    latency_seconds: float = 0.0
    tool_calls: int = 0
