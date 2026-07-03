"""Deterministic question→answer constraint checks (no LLM).

Extract high-confidence shape expectations from the question text and flag when the
candidate answer clearly contradicts them. Conservative by design — only flag when the
mismatch is unambiguous, to avoid false-positive reviser churn.
"""

from __future__ import annotations

import re

_TOP_N_RE = re.compile(r"\btop\s+(\d{1,3})\b", re.IGNORECASE)
_PERCENT_Q_RE = re.compile(r"\b(percentage|percent)\b", re.IGNORECASE)
_PERCENT_A_RE = re.compile(r"\d+(\.\d+)?\s*%|\bpercent\b", re.IGNORECASE)


def _answer_item_count(answer: str) -> int:
    """Conservative item count: prefer newlines (high confidence); else require a
    genuine comma/"and"/"&"-separated list (>=3 parts) before trusting the split —
    a 2-part split is too ambiguous (e.g. "Chicago, IL" is one answer, not two)."""
    lines = [ln for ln in answer.splitlines() if ln.strip()]
    if len(lines) > 1:
        return len(lines)
    parts = [p.strip() for p in re.split(r",|\band\b|&", answer, flags=re.IGNORECASE) if p.strip()]
    if len(parts) >= 3:
        return len(parts)
    return 1 if answer.strip() else 0


def check_answer_constraints(question: str, answer: str) -> list[str]:
    violations: list[str] = []
    m = _TOP_N_RE.search(question)
    if m:
        n = int(m.group(1))
        got = _answer_item_count(answer)
        # only flag a clear shortfall (agent listed materially fewer than asked); a count
        # of 1 is an overloaded "unambiguous single item OR ambiguous 2-part split" sentinel
        # from _answer_item_count, so it's never trusted enough to flag on its own.
        if 1 < got < n:
            violations.append(f"Question asks for the top {n}, but the answer lists {got} items.")
    if _PERCENT_Q_RE.search(question) and not _PERCENT_A_RE.search(answer):
        violations.append("Question asks for a percentage, but the answer has no percentage value.")
    return violations
