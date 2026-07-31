"""Benchmark-informed rule packs — content tests and the blocking contamination gate."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from labrat.eval.benchmarks.dab.informed_packs import (
    all_pack_lines,
    analytical_convention_lines,
    answer_shape_lines,
    per_dataset_lines,
    validator_shape_lines,
)

DAB = Path.home() / "repos" / "DataAgentBench"

# Value-shaped tokens (containing a digit or a separator) are checked from 3 chars up:
# ground truth is full of short values like "0.33", "12.5", "2024", "ID-9". Purely
# alphabetic tokens keep a 5-char floor so ordinary English does not drown the signal.
_VALUE_SHAPED = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./>@%-]{2,}")
_HAS_VALUE_MARK = re.compile(r"[\d_./>@%-]")
_ALPHA_WORD = re.compile(r"[A-Za-z]{5,}")
# Ordinary English and our own vocabulary — not evidence of leakage.
#
# Adding a word here permanently blinds the gate to it. Vet with BOTH corpora the gate
# scans — ground_truth.csv AND validate.py — under ~/repos/DataAgentBench:
#   grep -rn <word> ~/repos/DataAgentBench/query_*/query*/{ground_truth.csv,validate.py}
# Then classify the hit:
#   - the word is the graded value entire, or is topically distinctive of the answer
#     (it names the concept the answers are about) -> NEVER suppress; reword the rule.
#     ("other" IS a category value; "histology" names what the pancancer answers are.)
#   - the word appears inside a longer graded string but does not identify it
#     ("group" in a company name, "state" in a patent title, "after" in a patent
#     title) -> safe; a real leak surfaces as the distinctive part, not the common one.
#   - header-only hit -> safe ONLY IF the word also occurs in that query's question text
#     or db_description*.txt. GT headers here are invented answer-shape names
#     (total_readmes, mutation_percentage) that exist in no table, so "it is just a
#     column name" is NOT sufficient on its own.
#   - hit only outside the datasets in informed_packs._DATASETS -> corpus-glob noise; safe.
#   - hit only in validate.py -> validator code/prose; safe.
# Prefer rewording over suppressing whenever the rule survives it intact.
_STOPWORDS = frozenset(
    """about above after against already always another answer appear article because
    before between category check coded column columns compute computed contains
    context correct count counts derive derived different directly during either
    emit emitted enough every exactly example except explicit extract extracted
    field fields first following format formats-group group groups identifier
    identifiers immediately include included instead itself label labels match
    matching method moving named never normalize number numeric numbers order
    others output outputs period periods phrase precision present preserve
    punctuation question questions ranking rather report reported
    requested result results rounded separator separators should simply single
    source specific state stated string strings structure substring table tables
    temp their there these thing those through together toward under unless
    using validate value values verbatim where whether
    which while whitespace whole within without write""".split()
)


def test_answer_shape_pack_covers_its_three_measured_failures() -> None:
    """Derived from: pancancer_atlas:1 (we emitted names, expected form is codes),
    patents:2 (we emitted one group where the result is per-group), googlelocal:1
    (ordering). Assertions check the CONCEPT is present, not exact wording."""
    text = " ".join(answer_shape_lines()).lower()
    assert "code" in text and "name" in text  # identifier form
    assert "every" in text and "group" in text  # enumerate all groups
    assert "order" in text  # ranking order
    assert len(answer_shape_lines()) == 3


def test_validator_shape_pack_covers_adjacency_head_and_precision() -> None:
    """Derived from: googlelocal:2 (value must sit immediately after the name),
    stockindex:1/2 (answer must appear in the head of the output), stockindex:3
    (proximity), and repeated rounding mismatches."""
    text = " ".join(validator_shape_lines()).lower()
    assert "immediately" in text  # adjacency
    assert "first" in text  # head placement
    assert "precision" in text  # full + rounded
    assert len(validator_shape_lines()) == 3


def test_validator_shape_pack_instructs_adjacency_not_mere_co_occurrence() -> None:
    """Rule 3 targets a proximity failure: a correct value placed too far from its
    label scores zero. Wording that only implies co-occurrence ("together") satisfies
    a naive keyword check while losing the instruction, so pin the adjacency phrasing."""
    text = " ".join(validator_shape_lines()).lower()
    assert "side by side" in text or "immediately" in text
    assert "precision" in text


def test_analytical_conventions_pack_covers_the_four_recurring_choices() -> None:
    """Derived from: patents:2 (smoothed-average seeding and empty periods),
    deps_dev_v1:1 (composite path identifiers), deps_dev_v1:2 (prose-embedded value
    extraction and substring-join blowup), and corrupted join keys."""
    text = " ".join(analytical_convention_lines()).lower()
    assert "moving average" in text or "smoothed" in text
    assert "join" in text
    assert "temp table" in text
    assert "composite" in text or "separator" in text
    assert len(analytical_convention_lines()) == 4


def test_analytical_conventions_use_precise_technical_vocabulary() -> None:
    """Dodging gate tokens once pushed these rules into vague paraphrase ("fragment
    test" for substring, "trailing blank gaps" for whitespace), which a model cannot
    reliably map onto the operations meant. "match rate" additionally matches the
    verify_join tool's own field name, so the instruction and the tool agree."""
    text = " ".join(analytical_convention_lines()).lower()
    for term in (
        "substring",
        "whitespace",
        "punctuation",
        "word-boundary",
        "temp table",
        "match rate",
    ):
        assert term in text


def test_per_dataset_pack_returns_rules_only_for_covered_datasets() -> None:
    """Highest-variance pack and the most likely to be dropped after ablation.
    Naming a dataset is permitted; naming its answer is not — the contamination gate
    enforces that."""
    assert per_dataset_lines("github_repos"), "covered dataset must yield rules"
    assert per_dataset_lines("pancancer_atlas")
    assert per_dataset_lines("bookreview") == [], "uncovered dataset yields nothing"
    assert per_dataset_lines("nonexistent_dataset") == []


def test_per_dataset_pack_states_population_scoping_for_file_questions() -> None:
    text = " ".join(per_dataset_lines("github_repos")).lower()
    assert "population" in text or "sampled" in text


def test_no_pack_rule_contains_a_numeral() -> None:
    """A concrete number inside a form-only rule is a content risk the string-matching
    gate cannot judge on principle: a numeral cannot be classified as form vs content by
    shape alone, so it must never appear at all, regardless of whether this particular
    digit happens to collide with the corpus today."""
    assert not any(ch.isdigit() for line in all_pack_lines() for ch in line)


def _tokens_from(lines: list[str]) -> set[str]:
    """Extract candidate tokens to check against the ground-truth corpus.

    The two scans run INDEPENDENTLY and their results union, which means a
    hyphenated compound also yields its alphabetic parts ("well-known" -> also
    "known"). That is deliberate over-blocking, not an oversight. Suppressing the
    sub-parts was tried and reverted: it made a real corpus term glued to prose by a
    separator ("Astrocytoma-related") invisible to the blob check, because only the
    whole compound was tested and the compound is not in the corpus.

    For a BLOCKING contamination gate a missed leak is catastrophic and a noisy build
    is merely annoying, so this errs toward flagging too much. If a pack line trips it
    on innocent prose, reword the rule or add the specific word to _STOPWORDS — do not
    reintroduce span suppression.
    """
    out: set[str] = set()
    for line in lines:
        for m in _VALUE_SHAPED.finditer(line):
            tok = m.group(0).rstrip(".,;:")  # strip BEFORE the value-mark test:
            if not tok or not _HAS_VALUE_MARK.search(tok):
                # a trailing period does not make a word value-shaped
                continue
            if tok.lower() not in _STOPWORDS:
                out.add(tok)
        for m in _ALPHA_WORD.finditer(line):
            tok = m.group(0)
            # No rstrip here: _ALPHA_WORD matches letters only, so a match can never
            # carry trailing punctuation. The value-shaped loop above DOES need it.
            if tok.lower() not in _STOPWORDS:
                out.add(tok)
    return out


def test_sentence_final_words_are_filtered_by_the_plain_stopword() -> None:
    """A trailing period must not create a second token that evades the stopword list.
    Enumerating period-suffixed variants would grow a permanent blind spot in the gate
    once per pack."""
    assert _tokens_from(["Report the coded name."]) == set()
    assert _tokens_from(["Report the coded name"]) == set()


def test_stripping_does_not_damage_interior_punctuation() -> None:
    toks = _tokens_from(["values 0.33 and 12.5 end the line."])
    assert "0.33" in toks and "12.5" in toks


def test_trailing_period_does_not_make_a_short_word_value_shaped() -> None:
    """A sentence-final period once satisfied the value-mark test, turning every 3+
    character word ending a sentence into a value-shaped token. Bare short words then
    matched the corpus as substrings and had to be stopworded, growing blind spots in
    the gate for words like 'for' and 'line'."""
    assert _tokens_from(["Group the rows for."]) == _tokens_from(["Group the rows for"])
    assert "for" not in _tokens_from(["Group the rows for."])


def _tokens() -> set[str]:
    return _tokens_from(all_pack_lines())


def test_token_extractor_catches_short_value_shapes() -> None:
    """Regression: a 5-char floor let '0.33' — a literal ground-truth value for one of
    the benchmark tasks — pass the gate untouched. Short value-shaped tokens must be
    extracted, or the gate passes vacuously on exactly the leaks it exists to catch."""
    toks = _tokens_from(["values like 0.33 and 12.5 and 2024 and ID-9 and 45.6%"])
    for expected in ("0.33", "12.5", "2024", "ID-9"):
        assert expected in toks, f"{expected} must be extracted; got {sorted(toks)}"


def test_corpus_term_glued_to_prose_is_still_caught() -> None:
    """A real corpus term joined to other words by a separator must still be checked.
    Span suppression once made 'Astrocytoma-related' a single token that missed the
    corpus entirely, hiding a genuine leak. Both the compound AND its alphabetic parts
    must be extracted."""
    toks = _tokens_from(["Report the Astrocytoma-related tumor grade."])
    assert "Astrocytoma" in toks
    assert "Astrocytoma-related" in toks


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
