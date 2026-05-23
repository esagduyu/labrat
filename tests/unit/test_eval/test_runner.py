"""Unit tests for the eval runner (M26)."""

from __future__ import annotations

import pytest

from labrat.eval.models import EvalCase, EvalResult, EvalStatus
from labrat.eval.runner import EvalRunner

# ── fixtures ──────────────────────────────────────────────────────────────────


def make_case(
    id: str = "case-1",
    question: str = "How many orders?",
    expected_sql: str | None = "SELECT COUNT(*) FROM orders",
    gold_result: str | None = None,
) -> EvalCase:
    return EvalCase(id=id, question=question, expected_sql=expected_sql, gold_result=gold_result)


class StubSuite:
    suite_name = "stub"

    def __init__(self, cases: list[EvalCase]) -> None:
        self.cases: list[EvalCase] = cases


# ── EvalCase ──────────────────────────────────────────────────────────────────


def test_eval_case_requires_id_and_question() -> None:
    case = make_case()
    assert case.id == "case-1"
    assert case.question == "How many orders?"


def test_eval_case_optional_expected_sql() -> None:
    case = EvalCase(id="x", question="q")
    assert case.expected_sql is None


# ── EvalResult ────────────────────────────────────────────────────────────────


def test_eval_result_status_enum() -> None:
    assert EvalStatus.correct in EvalStatus
    assert EvalStatus.wrong in EvalStatus
    assert EvalStatus.error in EvalStatus
    assert EvalStatus.skipped in EvalStatus


def test_eval_result_fields() -> None:
    result = EvalResult(
        case_id="c1",
        status=EvalStatus.correct,
        generated_sql="SELECT 1",
        latency_seconds=0.5,
        tool_calls=2,
    )
    assert result.case_id == "c1"
    assert result.latency_seconds == 0.5
    assert result.tool_calls == 2


# ── EvalRunner ────────────────────────────────────────────────────────────────


def test_eval_runner_accepts_suite_protocol() -> None:
    suite = StubSuite([make_case()])
    runner = EvalRunner(suite=suite)
    assert runner.suite is suite


@pytest.mark.asyncio
async def test_eval_runner_empty_suite_returns_empty_report() -> None:
    suite = StubSuite([])
    runner = EvalRunner(suite=suite)
    report = await runner.run()
    assert report.total == 0
    assert report.results == []


@pytest.mark.asyncio
async def test_eval_runner_skips_on_agent_error() -> None:
    """Runner marks cases as 'error' when the agent raises."""
    case = make_case()
    suite = StubSuite([case])

    async def _failing_agent(question: str) -> str:
        raise RuntimeError("agent broke")

    runner = EvalRunner(suite=suite, agent_fn=_failing_agent)
    report = await runner.run()
    assert report.total == 1
    assert report.results[0].status == EvalStatus.error


@pytest.mark.asyncio
async def test_eval_runner_marks_correct_when_sql_matches() -> None:
    """When agent returns expected SQL, result is correct."""
    case = make_case(expected_sql="SELECT 1")
    suite = StubSuite([case])

    async def _agent(question: str) -> str:
        return "SELECT 1"

    runner = EvalRunner(suite=suite, agent_fn=_agent)
    report = await runner.run()
    assert report.results[0].status == EvalStatus.correct


@pytest.mark.asyncio
async def test_eval_runner_marks_wrong_when_sql_differs() -> None:
    case = make_case(expected_sql="SELECT COUNT(*) FROM orders")
    suite = StubSuite([case])

    async def _agent(question: str) -> str:
        return "SELECT 1"

    runner = EvalRunner(suite=suite, agent_fn=_agent)
    report = await runner.run()
    assert report.results[0].status == EvalStatus.wrong


@pytest.mark.asyncio
async def test_eval_runner_records_latency() -> None:
    case = make_case()
    suite = StubSuite([case])

    async def _agent(question: str) -> str:
        return "SELECT 1"

    runner = EvalRunner(suite=suite, agent_fn=_agent)
    report = await runner.run()
    assert report.results[0].latency_seconds >= 0.0
