"""Tests for the LLM-as-judge verifier and its AgentLoop integration (Pillar 1)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from labrat.agent.loop import AgentLoop, ContentBlock, TextBlock
from labrat.agent.providers.base import ModelProvider
from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.agent.verifier import LLMVerifier, Verdict, parse_verdict, provider_llm_fn


class _MockProvider(ModelProvider):
    """Emits a pre-configured sequence of response blocks, one per stream() call."""

    def __init__(self, responses: list[list[ContentBlock]]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[ContentBlock]:
        blocks = self._responses[self.calls] if self.calls < len(self._responses) else []
        self.calls += 1

        async def _iter() -> AsyncIterator[ContentBlock]:
            for b in blocks:
                yield b

        return _iter()


class _ScriptedVerifier:
    """Returns verdicts from a list; defaults to sufficient once exhausted."""

    def __init__(self, verdicts: list[Verdict]) -> None:
        self._verdicts = list(verdicts)
        self.calls = 0
        self.seen: list[tuple[str, str]] = []

    async def verify(
        self, *, question: str, answer: str, transcript: list[dict[str, Any]]
    ) -> Verdict:
        self.seen.append((question, answer))
        v = self._verdicts[self.calls] if self.calls < len(self._verdicts) else Verdict(True)
        self.calls += 1
        return v


def _ctx() -> ToolContext:
    return ToolContext(connection=object(), catalog=object())


# ── parse_verdict ──────────────────────────────────────────────────────────────


def test_parse_verdict_sufficient() -> None:
    assert parse_verdict("sufficient") == Verdict(sufficient=True)


def test_parse_verdict_insufficient_with_feedback() -> None:
    v = parse_verdict("insufficient: you never ran the query")
    assert not v.sufficient
    assert v.feedback == "you never ran the query"


def test_parse_verdict_fails_open_on_garbage() -> None:
    assert parse_verdict("uh, maybe?").sufficient is True


# ── LLMVerifier ──────────────────────────────────────────────────────────────


async def test_llm_verifier_parses_llm_reply() -> None:
    async def fake_llm(prompt: str) -> str:
        assert "User question" in prompt and "final answer" in prompt
        return "insufficient: missing the totals"

    verdict = await LLMVerifier(fake_llm).verify(question="q", answer="a")
    assert not verdict.sufficient
    assert verdict.feedback == "missing the totals"


async def test_provider_llm_fn_concatenates_text() -> None:
    provider = _MockProvider([[TextBlock(text="suffic"), TextBlock(text="ient")]])
    fn = provider_llm_fn(provider)
    assert await fn("anything") == "sufficient"


# ── AgentLoop integration ──────────────────────────────────────────────────────


async def test_loop_without_verifier_stops_immediately() -> None:
    provider = _MockProvider([[TextBlock(text="done")]])
    loop = AgentLoop(provider=provider, registry=ToolRegistry(), ctx=_ctx())
    await loop.run("q")
    assert loop.verify_rounds_used == 0
    assert provider.calls == 1


async def test_loop_continues_when_verifier_says_insufficient() -> None:
    provider = _MockProvider([[TextBlock(text="first")], [TextBlock(text="second")]])
    verifier = _ScriptedVerifier([Verdict(False, "do better"), Verdict(True)])
    statuses: list[str] = []
    texts: list[str] = []
    loop = AgentLoop(provider=provider, registry=ToolRegistry(), ctx=_ctx(), verifier=verifier)

    await loop.run("q", on_text=texts.append, on_status=statuses.append)

    assert loop.verify_rounds_used == 1
    assert verifier.calls == 2  # checked the first answer, then the second
    assert "".join(texts) == "firstsecond"
    assert statuses == ["verifier: insufficient — do better"]
    # the feedback was injected as a user turn between the two assistant rounds
    assert any(m["role"] == "user" and "INSUFFICIENT" in str(m["content"]) for m in loop.history)


async def test_loop_respects_max_verify_rounds() -> None:
    provider = _MockProvider([[TextBlock(text="a")], [TextBlock(text="b")], [TextBlock(text="c")]])
    verifier = _ScriptedVerifier([Verdict(False, "x"), Verdict(False, "y"), Verdict(False, "z")])
    loop = AgentLoop(
        provider=provider,
        registry=ToolRegistry(),
        ctx=_ctx(),
        verifier=verifier,
        max_verify_rounds=1,
    )
    await loop.run("q")
    # one re-prompt allowed; the second would-be-final answer is accepted as-is
    assert loop.verify_rounds_used == 1
    assert verifier.calls == 1
    # ...but that final answer was NEVER re-checked — the round budget ran
    # out before a re-verify could happen, so callers must not report this
    # turn as verifier-passed (whole-branch F2).
    assert loop.verify_exhausted is True


async def test_loop_verify_exhausted_false_when_verdict_passes() -> None:
    """A verdict that genuinely passes (first try or after a re-prompt) must
    NOT be flagged as exhausted."""
    provider = _MockProvider([[TextBlock(text="only")]])
    verifier = _ScriptedVerifier([Verdict(True)])
    loop = AgentLoop(provider=provider, registry=ToolRegistry(), ctx=_ctx(), verifier=verifier)
    await loop.run("q")
    assert loop.verify_rounds_used == 0
    assert loop.verify_exhausted is False


async def test_loop_skips_verifier_when_turn_budget_spent() -> None:
    provider = _MockProvider([[TextBlock(text="only")]])
    verifier = _ScriptedVerifier([Verdict(False, "more")])
    loop = AgentLoop(
        provider=provider,
        registry=ToolRegistry(),
        ctx=_ctx(),
        verifier=verifier,
        max_turns=1,
    )
    await loop.run("q")
    # turn budget is spent after the single round, so we don't append un-answerable feedback
    assert loop.verify_rounds_used == 0
    assert verifier.calls == 0
