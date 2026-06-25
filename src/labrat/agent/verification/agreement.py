"""answers_agree — LLM-judge equivalence for two free-text data answers.

The single agreement primitive shared by consensus voting and the re-derive stage.
The benchmark validator can't be used at inference (it's the answer key → leakage),
so agreement is judged answer-to-answer. Fail-open: any parse error or judge
exception counts as 'agree' so verification can never drop a correct answer.
"""

from __future__ import annotations

from labrat.agent.verifier import LLMFn


def _parse_agreement(raw: str) -> bool:
    """True unless the judge clearly says the answers differ. Fail-open."""
    s = raw.strip().lower()
    # accept only an explicit, unambiguous "different" verdict as disagreement
    if s.startswith("different"):
        return False
    return True


async def answers_agree(a: str, b: str, *, question: str, llm_fn: LLMFn) -> bool:
    """Whether answers a and b express the same result for ``question``."""
    if a.strip() == b.strip():
        return True
    prompt = (
        "You compare two answers to the same data question and decide whether they "
        "express the SAME result. Ignore wording, formatting, units phrasing, and "
        "ordering — judge only whether the underlying result is the same.\n\n"
        f"Question:\n{question}\n\nAnswer A:\n{a}\n\nAnswer B:\n{b}\n\n"
        'Reply with EXACTLY one word: "same" or "different".'
    )
    try:
        raw = await llm_fn(prompt)
    except Exception:
        return True  # fail-open: never drop an answer on a judge error
    return _parse_agreement(raw)
