"""Consensus clustering / modal-answer selection (FEATURE: verification layer)."""

from __future__ import annotations

from labrat.agent.verification.consensus import choose_modal


def _equal_judge():
    # judges "same" iff the raw strings are equal (lets tests control clustering)
    async def _fn(prompt: str) -> str:
        return "different"  # answers_agree short-circuits exact matches before calling

    return _fn


async def test_majority_wins() -> None:
    idx, low = await choose_modal(["A", "B", "A"], question="q", llm_fn=_equal_judge())
    assert idx == 0  # "A" cluster (size 2) beats "B" (size 1); first "A" is index 0
    assert low is False


async def test_tie_breaks_to_primary_low_confidence() -> None:
    idx, low = await choose_modal(["A", "B"], question="q", llm_fn=_equal_judge())
    assert idx == 0
    assert low is True  # tie → primary, flagged low-confidence


async def test_all_distinct_is_low_confidence() -> None:
    idx, low = await choose_modal(["A", "B", "C"], question="q", llm_fn=_equal_judge())
    assert idx == 0 and low is True


async def test_single_answer_passthrough() -> None:
    idx, low = await choose_modal(["only"], question="q", llm_fn=_equal_judge())
    assert idx == 0 and low is True  # k=1 → no real consensus
