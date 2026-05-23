"""Eval runner: orchestrates case execution (M26)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from labrat.eval.models import EvalCase, EvalResult, EvalStatus
from labrat.eval.report import EvalReport


class EvalSuiteProtocol(Protocol):
    suite_name: str
    cases: list[EvalCase]


AgentFn = Callable[[str], Awaitable[str]]


def _sql_matches(generated: str, expected: str) -> bool:
    """Normalised SQL comparison (case- and whitespace-insensitive)."""

    def _norm(s: str) -> str:
        return " ".join(s.upper().split())

    return _norm(generated) == _norm(expected)


class EvalRunner:
    """Runs an eval suite and returns an EvalReport."""

    def __init__(
        self,
        suite: Any,
        agent_fn: AgentFn | None = None,
    ) -> None:
        self.suite = suite
        self._agent_fn = agent_fn

    async def run(self) -> EvalReport:
        results: list[EvalResult] = []
        for case in self.suite.cases:
            result = await self._run_case(case)
            results.append(result)
        return EvalReport(suite_name=self.suite.suite_name, results=results)

    async def _run_case(self, case: EvalCase) -> EvalResult:
        if self._agent_fn is None:
            return EvalResult(
                case_id=case.id,
                status=EvalStatus.skipped,
                generated_sql=None,
            )

        start = time.monotonic()
        try:
            generated_sql = await self._agent_fn(case.question)
            latency = time.monotonic() - start
        except Exception as exc:
            return EvalResult(
                case_id=case.id,
                status=EvalStatus.error,
                generated_sql=None,
                error_message=str(exc),
                latency_seconds=time.monotonic() - start,
            )

        if case.expected_sql is None:
            status = EvalStatus.skipped
        elif _sql_matches(generated_sql, case.expected_sql):
            status = EvalStatus.correct
        else:
            status = EvalStatus.wrong

        return EvalResult(
            case_id=case.id,
            status=status,
            generated_sql=generated_sql,
            latency_seconds=latency,
        )
