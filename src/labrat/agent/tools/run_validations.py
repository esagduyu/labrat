"""run_validations tool: LLM-driven rule checker against query results (M32)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from labrat.agent.tools.base import Tool, ToolContext
from labrat.validations.checker import ValidationChecker
from labrat.validations.store import ValidationRuleStore


class _Input(BaseModel):
    sql: str
    result_summary: str


class _Output(BaseModel):
    ok: bool
    passed: int
    warned: int
    blocked: int
    details: list[dict[str, Any]]


class RunValidationsTool(Tool[_Input]):
    """Check the last query result against the profile's custom validation rules.

    Returns a summary of pass/warn/block outcomes.  The agent should surface
    any 'block' results to the user immediately.
    """

    @property
    def name(self) -> str:
        return "run_validations"

    @property
    def description(self) -> str:
        return (
            "Evaluate the profile's custom validation rules against a SQL query "
            "and its result summary.  Call this after running a query to check "
            "for constraint violations.  Returns pass/warn/block breakdown."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        store = ValidationRuleStore()
        rules = store.active_rules(ctx.profile_name)
        if not rules:
            return _Output(ok=True, passed=0, warned=0, blocked=0, details=[])

        async def _llm(prompt: str) -> str:
            from labrat.agent.loop import TextBlock
            from labrat.agent.providers.claude_code import ClaudeCodeProvider

            provider = ClaudeCodeProvider()
            chunks: list[str] = []
            stream = await provider.stream(
                messages=[{"role": "user", "content": prompt}],
                system="You are a data validation agent. Reply with exactly one line.",
                tools=[],
            )
            async for block in stream:
                if isinstance(block, TextBlock):
                    chunks.append(block.text)
            return "".join(chunks).strip()

        checker = ValidationChecker(llm_fn=_llm)
        results = await checker.check(
            sql=args.sql,
            result_summary=args.result_summary,
            rules=rules,
        )

        from labrat.validations.model import ValidationStatus

        details = [
            {
                "rule": r.rule_text,
                "status": r.status.value,
                "explanation": r.explanation,
            }
            for r in results
        ]
        passed = sum(1 for r in results if r.status == ValidationStatus.pass_)
        warned = sum(1 for r in results if r.status == ValidationStatus.warn)
        blocked = sum(1 for r in results if r.status == ValidationStatus.block)
        return _Output(
            ok=blocked == 0,
            passed=passed,
            warned=warned,
            blocked=blocked,
            details=details,
        )
