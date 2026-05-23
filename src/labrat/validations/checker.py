"""ValidationChecker: LLM-driven rule evaluation subagent (M32).

Each rule is evaluated independently via a single LLM call that returns
"pass", "warn: <explanation>", or "block: <explanation>".
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from labrat.validations.model import ValidationRule, ValidationStatus

LLMFn = Callable[[str], Awaitable[str]]


@dataclass
class ValidationResult:
    """Outcome of checking one rule."""

    rule_id: str
    rule_text: str
    status: ValidationStatus
    explanation: str = ""


@dataclass
class CheckReport:
    """Aggregated check results for one query execution."""

    results: list[ValidationResult]

    @property
    def has_block(self) -> bool:
        return any(r.status == ValidationStatus.block for r in self.results)

    @property
    def has_warn(self) -> bool:
        return any(r.status == ValidationStatus.warn for r in self.results)

    def warnings(self) -> list[ValidationResult]:
        return [r for r in self.results if r.status == ValidationStatus.warn]

    def blocks(self) -> list[ValidationResult]:
        return [r for r in self.results if r.status == ValidationStatus.block]


def _parse_response(raw: str, rule: ValidationRule) -> ValidationResult:
    """Parse LLM response into a ValidationResult.

    Expected format: "pass" | "warn: <explanation>" | "block: <explanation>"
    Defaults to pass on unparseable responses.
    """
    stripped = raw.strip().lower()
    if stripped.startswith("block"):
        explanation = raw[raw.index(":") + 1 :].strip() if ":" in raw else ""
        return ValidationResult(
            rule_id=rule.id,
            rule_text=rule.natural_language_rule,
            status=ValidationStatus.block,
            explanation=explanation,
        )
    if stripped.startswith("warn"):
        explanation = raw[raw.index(":") + 1 :].strip() if ":" in raw else ""
        return ValidationResult(
            rule_id=rule.id,
            rule_text=rule.natural_language_rule,
            status=ValidationStatus.warn,
            explanation=explanation,
        )
    return ValidationResult(
        rule_id=rule.id,
        rule_text=rule.natural_language_rule,
        status=ValidationStatus.pass_,
    )


class ValidationChecker:
    """Evaluate validation rules against a query and its result summary."""

    def __init__(self, llm_fn: LLMFn) -> None:
        self._llm_fn = llm_fn

    async def check(
        self,
        sql: str,
        result_summary: str,
        rules: list[ValidationRule],
    ) -> list[ValidationResult]:
        results: list[ValidationResult] = []
        for rule in rules:
            prompt = (
                "You are a data validation agent. Check whether the following rule is violated.\n\n"
                f"Rule: {rule.natural_language_rule}\n"
                f"Severity: {rule.severity.value}\n\n"
                f"SQL query:\n{sql}\n\n"
                f"Result summary:\n{result_summary}\n\n"
                "Reply with exactly one of:\n"
                '  "pass" — rule is satisfied\n'
                '  "warn: <short explanation>" — rule is violated (warn)\n'
                '  "block: <short explanation>" — rule is violated (block)\n'
            )
            raw = await self._llm_fn(prompt)
            result = _parse_response(raw, rule)
            results.append(result)
        return results
