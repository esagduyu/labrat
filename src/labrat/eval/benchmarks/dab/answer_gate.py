"""Deterministic answer-shape checks for the DAB claude-mcp path.

Why this exists: three prose levers aimed at delivery shape have now been measured,
and prose keeps losing. One task in the 2026-07 gap analysis passed and failed on
nothing but a reformatted time range; the adjacent-token lever shipped and did not
prevent it; the convention-pinning lever never moved its only target across two full
runs and was dropped. Meanwhile the leaderboard's 0.80+ band pairs authored guidance
with deterministic machinery — one team lifted an identical backbone by ~4pp on
harness changes alone. A check written in code cannot forget on trial 3 of 5.
Full evidence: docs/dab-sonnet5-vs-luna-gap-analysis.md sections (f), (i) and (k).

Scope discipline (enforced by tests in ``tests/unit/test_dab_answer_gate.py``):
every check must stand up as GENERAL answer quality. Nothing here may encode a value,
label, threshold or cardinality drawn from benchmark ground truth, name a benchmark
dataset, or hard-code a grader window size reverse-engineered from the scorer.
Adjacency is kept as a structural rule ("don't bury the value in a table column")
rather than a character budget copied out of the harness — the former is defensible
on its own terms, the latter is scorer-fitting.

The gate is advisory: it returns violations for the caller to act on (typically one
bounded re-prompt). It never rewrites an answer, because silently reshaping a model's
output would make trace-level auditing meaningless.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

# "top 5", "5 most common", "list the three largest", "top-10"
_COUNT_PATTERNS = (
    re.compile(r"\btop[\s-]+(\d+|" + "|".join(_WORD_NUMBERS) + r")\b", re.I),
    re.compile(
        r"\b(\d+|"
        + "|".join(_WORD_NUMBERS)
        + r")\s+(?:most|least|largest|smallest|highest|lowest|best|worst)\b",
        re.I,
    ),
    re.compile(r"\blist\s+(?:the\s+)?(\d+|" + "|".join(_WORD_NUMBERS) + r")\b", re.I),
)

_FRACTION_REQUEST = re.compile(r"\b(fraction|proportion|ratio)\b", re.I)
_PERCENT_ONLY = re.compile(r"\d+(?:\.\d+)?\s*(?:%|\bpercent\b)", re.I)
_DECIMAL = re.compile(r"(?<![\d%])0?\.\d+")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$", re.M)
# an enumerated or bulleted item: "1. x", "1) x", "- x", "* x"
_LIST_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+\S", re.M)


@dataclass(frozen=True)
class Violation:
    """One deterministic finding about an answer's shape."""

    code: str
    detail: str


def requested_item_count(question: str) -> int | None:
    """Return the number of items the question explicitly asks for, if any.

    Only counts phrasings that name a cardinality of RESULTS ("top 5", "3 most
    common", "list the three ..."). Deliberately ignores incidental numbers such as
    years or thresholds — "average rating in 2016" and "rating above 4" request no
    particular number of items, and treating them as such would fire the shortfall
    check on almost every scalar question.
    """
    for pattern in _COUNT_PATTERNS:
        m = pattern.search(question)
        if not m:
            continue
        token = m.group(1).lower()
        if token.isdigit():
            return int(token)
        if token in _WORD_NUMBERS:
            return _WORD_NUMBERS[token]
    return None


def _count_delivered_items(answer: str) -> int:
    """Count list-shaped items in the answer (enumerated, bulleted, or table rows)."""
    items = len(_LIST_ITEM.findall(answer))
    if items:
        return items
    rows = _TABLE_ROW.findall(answer)
    # a markdown table carries a header row and a separator row before real data
    body = [r for r in rows if not re.match(r"^\s*\|[\s|:-]+\|\s*$", r)]
    return max(0, len(body) - 1) if len(body) > 1 else 0


def check_answer(*, question: str, answer: str) -> list[Violation]:
    """Return deterministic shape violations for ``answer`` given ``question``.

    An empty list means nothing detectable is wrong — NOT that the answer is correct.
    This gate says nothing about whether the analysis is right; it only catches
    delivery failures that we have observed costing otherwise-correct trials.
    """
    violations: list[Violation] = []

    if not answer.strip():
        return [Violation("empty_answer", "the answer is empty")]

    wanted = requested_item_count(question)
    if wanted is not None:
        delivered = _count_delivered_items(answer)
        if delivered and delivered < wanted:
            violations.append(
                Violation(
                    "count_shortfall",
                    f"the question asks for {wanted} items; the answer presents {delivered}",
                )
            )
        if _TABLE_ROW.search(answer):
            violations.append(
                Violation(
                    "table_delivery",
                    "items and their values are delivered in a markdown table, which "
                    "separates each value from its label; state them as adjacent plain "
                    "tokens instead",
                )
            )

    if _FRACTION_REQUEST.search(question):
        if _PERCENT_ONLY.search(answer) and not _DECIMAL.search(answer):
            violations.append(
                Violation(
                    "fraction_as_percentage",
                    "the question asks for a fraction/proportion but the answer gives "
                    "only a percentage; give the decimal value",
                )
            )

    return violations


def format_violations(violations: list[Violation]) -> str:
    """Render violations as a short corrective instruction for a re-prompt."""
    lines = [
        "Your answer has delivery problems that will cause it to be scored wrong, "
        "even if the analysis is correct. Fix ONLY the presentation — do not change "
        "your findings, and do not re-run the analysis:"
    ]
    lines += [f"  - {v.detail}" for v in violations]
    lines.append("Restate the final answer on the last line.")
    return "\n".join(lines)
