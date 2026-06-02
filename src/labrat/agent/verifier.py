"""Verifier: an LLM-as-judge sufficiency gate for the agent loop (north-star Pillar 1).

Before the AgentLoop finishes (the model emits a round with no tool calls), an optional
Verifier judges whether the answer actually addresses the user's question. If not, the
loop feeds the verifier's feedback back as a new user turn and lets the agent continue —
bounded by ``max_verify_rounds``. This is LabRat's first answer-quality gate; today the
loop stops the instant the model stops calling tools, with no check that the answer is
any good.

Mirrors ``labrat.validations.checker.ValidationChecker``: one constrained LLM call,
parsed to a small verdict. It is fail-open — an unparseable reply counts as sufficient,
so the verifier can never trap the loop.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

LLMFn = Callable[[str], Awaitable[str]]


@dataclass
class Verdict:
    """Outcome of a verifier check."""

    sufficient: bool
    feedback: str = ""


class Verifier(Protocol):
    """Judges whether an answer sufficiently addresses the question."""

    async def verify(
        self, *, question: str, answer: str, transcript: list[dict[str, Any]]
    ) -> Verdict: ...


def parse_verdict(raw: str) -> Verdict:
    """Parse a judge reply: ``"sufficient"`` | ``"insufficient: <feedback>"``.

    Fail-open: anything that doesn't clearly say "insufficient" is treated as
    sufficient, so a confused judge never traps the loop.
    """
    stripped = raw.strip().lower()
    if stripped.startswith("insufficient"):
        feedback = raw[raw.index(":") + 1 :].strip() if ":" in raw else ""
        return Verdict(sufficient=False, feedback=feedback)
    return Verdict(sufficient=True)


class LLMVerifier:
    """LLM-as-judge sufficiency verifier (one constrained call per check)."""

    def __init__(self, llm_fn: LLMFn) -> None:
        self._llm_fn = llm_fn

    async def verify(
        self,
        *,
        question: str,
        answer: str,
        transcript: list[dict[str, Any]] | None = None,
    ) -> Verdict:
        prompt = (
            "You are a strict reviewer checking whether a data analyst's answer fully "
            "addresses the user's question.\n\n"
            f"User question:\n{question}\n\n"
            f"Analyst's final answer:\n{answer}\n\n"
            "Does the answer directly and completely answer the question with a concrete "
            "result — not a plan, a promise, or a partial finding?\n"
            "Reply with EXACTLY one of:\n"
            '  "sufficient" — the answer fully addresses the question\n'
            '  "insufficient: <what is missing or wrong>" — otherwise\n'
        )
        raw = await self._llm_fn(prompt)
        return parse_verdict(raw)


def provider_llm_fn(provider: Any, *, system: str = "") -> LLMFn:
    """Adapt a ``ModelProvider`` into a one-shot ``LLMFn`` via a tool-less stream call.

    Lets the loop's existing provider double as the verifier's judge (same model and
    billing) without a second client.
    """

    async def _fn(prompt: str) -> str:
        from labrat.agent.loop import TextBlock  # local import avoids an import cycle

        parts: list[str] = []
        stream = await provider.stream(
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            system=system,
        )
        async for block in stream:
            if isinstance(block, TextBlock):
                parts.append(block.text)
        return "".join(parts)

    return _fn
