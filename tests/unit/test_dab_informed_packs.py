"""Benchmark-informed rule packs — content tests and the blocking contamination gate."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from labrat.eval.benchmarks.dab.informed_packs import all_pack_lines

DAB = Path.home() / "repos" / "DataAgentBench"

# Tokens worth checking: anything that looks like a value rather than prose.
_CANDIDATE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./>@-]{4,}")
# Ordinary English and our own vocabulary — not evidence of leakage.
_STOPWORDS = frozenset(
    """about above after against already always another answer because before
    between column columns compute computed context correct count counts derive derived
    different directly during either emit emitted enough every exactly example except
    explicit extract extracted field fields first following format formats-group groups
    identifier identifiers immediately include included instead itself label labels
    matching method never number numbers order others output outputs period periods
    precision present preserve question questions ranking rather report reported
    requested result results rounded separator separators should simply single source
    specific state stated string strings structure table tables temp their there
    these those through together toward under unless using value values verbatim
    where whether which while whole within without""".split()
)


def _tokens() -> set[str]:
    out: set[str] = set()
    for line in all_pack_lines():
        for m in _CANDIDATE.finditer(line):
            tok = m.group(0)
            if tok.lower() not in _STOPWORDS:
                out.add(tok)
    return out


@pytest.mark.skipif(not DAB.exists(), reason="DataAgentBench checkout not present")
def test_no_pack_token_appears_in_ground_truth_or_validators() -> None:
    """BLOCKING integrity gate.

    A prior lever shipped with the example token '5-11PM', which was a literal
    held-out ground-truth value. Packs may encode FORM ("report the code column")
    but never CONTENT (the code itself). This greps every value-shaped token in
    every pack against ground truth and validators; any hit fails the build.
    """
    corpus: list[str] = []
    for pattern in ("query_*/query*/ground_truth.csv", "query_*/query*/validate.py"):
        for path in DAB.glob(pattern):
            corpus.append(path.read_text(errors="replace").lower())
    assert corpus, "expected ground-truth/validator files to scan"
    blob = "\n".join(corpus)

    offenders = sorted(tok for tok in _tokens() if tok.lower() in blob)
    assert not offenders, (
        f"pack tokens found in DAB ground truth / validators: {offenders}. "
        "Packs must encode form, never content."
    )
