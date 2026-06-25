"""Consensus over N candidate answers: greedy cluster by answers_agree, pick the modal."""

from __future__ import annotations

from labrat.agent.verification.agreement import answers_agree
from labrat.agent.verifier import LLMFn


async def choose_modal(answers: list[str], *, question: str, llm_fn: LLMFn) -> tuple[int, bool]:
    """Return (index of the modal answer, low_confidence).

    Greedy clustering: each answer joins the first cluster whose representative it
    agrees with, else seeds a new cluster. Modal = largest cluster (ties → earliest,
    so index 0 / the primary sub-run wins). low_confidence when the modal cluster has
    no majority (tie or all-distinct).
    """
    if not answers:
        return 0, True
    clusters: list[list[int]] = []  # each is a list of answer indices
    for i, ans in enumerate(answers):
        placed = False
        for cluster in clusters:
            rep = answers[cluster[0]]
            if await answers_agree(rep, ans, question=question, llm_fn=llm_fn):
                cluster.append(i)
                placed = True
                break
        if not placed:
            clusters.append([i])
    biggest = max(clusters, key=len)
    modal_index = biggest[0]
    # low confidence: no cluster has >1 member, or the winning cluster isn't a strict majority
    low_confidence = len(biggest) == 1 or len(biggest) * 2 <= len(answers)
    return modal_index, low_confidence
