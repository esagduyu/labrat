"""Benchmark-informed rule packs — content tests and the blocking contamination gate."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from labrat.eval.benchmarks.dab.informed_packs import all_pack_lines

DAB = Path.home() / "repos" / "DataAgentBench"

# Value-shaped tokens (containing a digit or a separator) are checked from 3 chars up:
# ground truth is full of short values like "0.33", "12.5", "2024", "ID-9". Purely
# alphabetic tokens keep a 5-char floor so ordinary English does not drown the signal.
_VALUE_SHAPED = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./>@%-]{2,}")
_HAS_VALUE_MARK = re.compile(r"[\d_./>@%-]")
_ALPHA_WORD = re.compile(r"[A-Za-z]{5,}")
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


def _tokens_from(lines: list[str]) -> set[str]:
    out: set[str] = set()
    for line in lines:
        consumed: list[tuple[int, int]] = []
        for m in _VALUE_SHAPED.finditer(line):
            tok = m.group(0)
            if not _HAS_VALUE_MARK.search(tok):
                # Not value-shaped after all: leave this span open to the alpha
                # scan below, or pure-alpha ground-truth labels stop being checked.
                continue
            consumed.append(m.span())
            if tok.lower() not in _STOPWORDS:
                out.add(tok)
        for m in _ALPHA_WORD.finditer(line):
            if any(start <= m.start() < end for start, end in consumed):
                continue
            tok = m.group(0)
            if tok.lower() not in _STOPWORDS:
                out.add(tok)
    return out


def _tokens() -> set[str]:
    return _tokens_from(all_pack_lines())


def test_token_extractor_catches_short_value_shapes() -> None:
    """Regression: a 5-char floor let '0.33' — a literal ground-truth value for one of
    the benchmark tasks — pass the gate untouched. Short value-shaped tokens must be
    extracted, or the gate passes vacuously on exactly the leaks it exists to catch."""
    toks = _tokens_from(["values like 0.33 and 12.5 and 2024 and ID-9 and 45.6%"])
    for expected in ("0.33", "12.5", "2024", "ID-9"):
        assert expected in toks, f"{expected} must be extracted; got {sorted(toks)}"


def test_hyphenated_compounds_do_not_spawn_extra_alpha_tokens() -> None:
    """A hyphenated compound is one token, not three. Independent scans previously
    re-extracted 'known' and 'insensitive' from 'well-known'/'case-insensitive',
    inflating the false-positive surface against the ground-truth corpus."""
    toks = _tokens_from(["Use a well-known convention for case-insensitive matching."])
    assert "well-known" in toks
    assert "known" not in toks
    assert "insensitive" not in toks


def test_pure_alpha_tokens_are_still_checked() -> None:
    """Guards the trap in the suppression fix: _VALUE_SHAPED also matches pure-alpha
    words, which are discarded by the value-mark filter. If their spans were treated as
    consumed, a pure-alpha ground-truth label would stop being checked and a real leak
    would pass. 'Astrocytoma' is exactly that shape."""
    toks = _tokens_from(["The Astrocytoma label must still be extracted."])
    assert "Astrocytoma" in toks


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
