"""LLM-judge answer-equivalence primitive (FEATURE: verification layer)."""

from __future__ import annotations

from labrat.agent.verification.agreement import answers_agree


def _judge(reply: str):
    async def _fn(prompt: str) -> str:
        return reply

    return _fn


async def test_agree_when_judge_says_same() -> None:
    assert (
        await answers_agree(
            "72", "there are 72 CPC codes", question="how many?", llm_fn=_judge("same")
        )
        is True
    )


async def test_disagree_when_judge_says_different() -> None:
    assert (
        await answers_agree("72", "73", question="how many?", llm_fn=_judge("different")) is False
    )


async def test_fail_open_on_garbage_verdict() -> None:
    # unparseable judge reply must count as agree (never drop a correct answer)
    assert await answers_agree("72", "73", question="q", llm_fn=_judge("uhh not sure")) is True


async def test_fail_open_on_judge_exception() -> None:
    async def _boom(prompt: str) -> str:
        raise RuntimeError("judge down")

    assert await answers_agree("a", "b", question="q", llm_fn=_boom) is True


async def test_identical_strings_short_circuit_no_llm() -> None:
    calls = {"n": 0}

    async def _count(prompt: str) -> str:
        calls["n"] += 1
        return "different"

    assert await answers_agree("42", "42", question="q", llm_fn=_count) is True
    assert calls["n"] == 0  # exact match needs no judge call
