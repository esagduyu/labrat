"""Naive one-shot NL→SQL baseline (M27).

Sends the user question directly to an LLM with a minimal prompt — no agent
loop, no schema exploration, no tool use. Establishes the lower-bound
comparison for LabRat's agentic approach.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from labrat.eval.models import EvalCase, EvalResult, EvalStatus
from labrat.eval.report import EvalReport

LLMFn = Callable[[str], Awaitable[str]]

_PROMPT_TEMPLATE = (
    "You are a SQL expert. Given the following question, write a single SQL query.\n"
    "Return ONLY the SQL query, no explanation.\n\n"
    "Question: {question}"
)


def _sql_matches(generated: str, expected: str) -> bool:
    def _norm(s: str) -> str:
        return " ".join(s.upper().split())

    return _norm(generated) == _norm(expected)


class NaiveShotBaseline:
    """One-shot NL→SQL via a plain LLM call (no agent loop)."""

    name = "naive-one-shot"

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        llm_fn: LLMFn | None = None,
    ) -> None:
        self._model = model
        self._llm_fn = llm_fn or self._default_llm

    async def _default_llm(self, prompt: str) -> str:
        import anthropic  # type: ignore[import-untyped]

        client = anthropic.AsyncAnthropic()
        msg = await client.messages.create(  # pyright: ignore[reportUnknownMemberType]
            model=self._model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return str(msg.content[0].text)  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType,reportUnknownArgumentType]

    async def run(self, cases: list[EvalCase]) -> EvalReport:
        results: list[EvalResult] = []
        for case in cases:
            result = await self._run_case(case)
            results.append(result)
        return EvalReport(suite_name=f"naive-{self._model}", results=results)

    async def _run_case(self, case: EvalCase) -> EvalResult:
        prompt = _PROMPT_TEMPLATE.format(question=case.question)
        start = time.monotonic()
        try:
            generated = await self._llm_fn(prompt)
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
        elif _sql_matches(generated, case.expected_sql):
            status = EvalStatus.correct
        else:
            status = EvalStatus.wrong

        return EvalResult(
            case_id=case.id,
            status=status,
            generated_sql=generated,
            latency_seconds=latency,
        )
