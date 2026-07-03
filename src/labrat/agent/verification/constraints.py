"""Deterministic question→answer constraint checks (no LLM).

Extract high-confidence shape expectations from the question text and flag when the
candidate answer clearly contradicts them. Conservative by design — only flag when the
mismatch is unambiguous, to avoid false-positive reviser churn.
"""

from __future__ import annotations

import re

_TOP_N_RE = re.compile(r"\btop\s+(\d{1,3})\b", re.IGNORECASE)
_PERCENT_Q_RE = re.compile(r"\b(percentage|percent|what\s+%|proportion)\b", re.IGNORECASE)
_PERCENT_A_RE = re.compile(r"\d+(\.\d+)?\s*%|\bpercent\b", re.IGNORECASE)


def _answer_item_count(answer: str) -> int:
    """Rough item count: prefer newlines, else commas, else 1 for a non-empty scalar."""
    lines = [ln for ln in answer.splitlines() if ln.strip()]
    if len(lines) > 1:
        return len(lines)
    parts = [p for p in answer.split(",") if p.strip()]
    if len(parts) > 1:
        return len(parts)
    return 1 if answer.strip() else 0


def check_answer_constraints(question: str, answer: str) -> list[str]:
    violations: list[str] = []
    m = _TOP_N_RE.search(question)
    if m:
        n = int(m.group(1))
        got = _answer_item_count(answer)
        # only flag a clear shortfall (agent listed materially fewer than asked)
        if 0 < got < n:
            violations.append(f"Question asks for the top {n}, but the answer lists {got} items.")
    if _PERCENT_Q_RE.search(question) and not _PERCENT_A_RE.search(answer):
        violations.append("Question asks for a percentage, but the answer has no percentage value.")
    return violations
