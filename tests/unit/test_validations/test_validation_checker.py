"""Tests for ValidationChecker (M32) — LLM mocked."""

from __future__ import annotations

import pytest

from labrat.validations.model import ValidationRule, ValidationSeverity, ValidationStatus


def _rule(
    text: str = "WAU should never exceed 10B",
    severity: ValidationSeverity = ValidationSeverity.warn,
) -> ValidationRule:
    return ValidationRule(
        profile="dev",
        natural_language_rule=text,
        severity=severity,
        enabled=True,
    )


# ── ValidationChecker ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_checker_returns_pass_when_no_violation() -> None:
    from labrat.validations.checker import ValidationChecker

    async def _mock_llm(prompt: str) -> str:
        return "pass"

    checker = ValidationChecker(llm_fn=_mock_llm)
    results = await checker.check(
        sql="SELECT COUNT(*) FROM events",
        result_summary="count: 1000",
        rules=[_rule()],
    )
    assert len(results) == 1
    assert results[0].status == ValidationStatus.pass_


@pytest.mark.asyncio
async def test_checker_returns_warn_on_violation() -> None:
    from labrat.validations.checker import ValidationChecker

    async def _mock_llm(prompt: str) -> str:
        return "warn: WAU is 15B which exceeds the 10B threshold"

    checker = ValidationChecker(llm_fn=_mock_llm)
    results = await checker.check(
        sql="SELECT wau FROM metrics",
        result_summary="wau: 15000000000",
        rules=[_rule()],
    )
    assert results[0].status == ValidationStatus.warn
    assert "15B" in results[0].explanation


@pytest.mark.asyncio
async def test_checker_returns_block_for_block_severity_rule() -> None:
    from labrat.validations.checker import ValidationChecker

    async def _mock_llm(prompt: str) -> str:
        return "block: query touches PII columns without consent filter"

    checker = ValidationChecker(llm_fn=_mock_llm)
    results = await checker.check(
        sql="SELECT email FROM users",
        result_summary="10 rows",
        rules=[_rule("never expose PII columns", severity=ValidationSeverity.block)],
    )
    assert results[0].status == ValidationStatus.block


@pytest.mark.asyncio
async def test_checker_returns_empty_for_no_rules() -> None:
    from labrat.validations.checker import ValidationChecker

    async def _mock_llm(prompt: str) -> str:
        return "pass"

    checker = ValidationChecker(llm_fn=_mock_llm)
    results = await checker.check(
        sql="SELECT 1",
        result_summary="1",
        rules=[],
    )
    assert results == []


@pytest.mark.asyncio
async def test_checker_checks_each_rule_independently() -> None:
    from labrat.validations.checker import ValidationChecker

    call_count = 0

    async def _mock_llm(prompt: str) -> str:
        nonlocal call_count
        call_count += 1
        return "pass"

    rules = [_rule("rule 1"), _rule("rule 2"), _rule("rule 3")]
    checker = ValidationChecker(llm_fn=_mock_llm)
    results = await checker.check(sql="SELECT 1", result_summary="1", rules=rules)
    assert len(results) == 3
    assert call_count == 3


@pytest.mark.asyncio
async def test_checker_has_block_property() -> None:
    from labrat.validations.checker import CheckReport, ValidationChecker

    async def _mock_llm(prompt: str) -> str:
        return "block: violation"

    checker = ValidationChecker(llm_fn=_mock_llm)
    results = await checker.check(
        sql="SELECT 1",
        result_summary="1",
        rules=[_rule(severity=ValidationSeverity.block)],
    )
    report = CheckReport(results=results)
    assert report.has_block is True
    assert report.has_warn is False


@pytest.mark.asyncio
async def test_checker_warn_property() -> None:
    from labrat.validations.checker import CheckReport, ValidationChecker

    async def _mock_llm(prompt: str) -> str:
        return "warn: something is off"

    checker = ValidationChecker(llm_fn=_mock_llm)
    results = await checker.check(
        sql="SELECT 1",
        result_summary="1",
        rules=[_rule()],
    )
    report = CheckReport(results=results)
    assert report.has_warn is True
    assert report.has_block is False
