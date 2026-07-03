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


def _line_split_count(line: str) -> int:
    """A single line's comma/"and"/"&"-separated part count, but only when it yields
    >=3 parts — a 2-part split is too ambiguous (e.g. "Chicago, IL" is one answer, not
    two) to trust as a list. Returns 0 when the line isn't a genuine list."""
    parts = [p.strip() for p in re.split(r",|\band\b|&", line, flags=re.IGNORECASE) if p.strip()]
    return len(parts) if len(parts) >= 3 else 0


def _answer_item_count(answer: str) -> int:
    """Conservative item count that avoids the preamble+list false positive: a
    preamble line ("The top 5 products are:") followed by a comma-separated list line
    must count as 5 items, not 2 lines. We take the max of (a) the newline count
    (high-confidence when every line is itself one item, e.g. "A\\nB\\nC") and (b) the
    best single-line comma/"and"/"&" split across all lines (high-confidence when one
    line is itself the whole list)."""
    lines = [ln for ln in answer.splitlines() if ln.strip()]
    newline_item_count = len(lines)
    best_single_line_split = max((_line_split_count(ln) for ln in lines), default=0)
    return max(newline_item_count, best_single_line_split, 1 if answer.strip() else 0)


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
