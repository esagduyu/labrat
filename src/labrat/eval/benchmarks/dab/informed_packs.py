"""Benchmark-informed rule packs for DAB.

Each pack is independently toggled so it can be ablated on its own and dropped if
it does not earn its place. All default OFF at the call sites in ``suite.py``.

INVARIANT, enforced by ``tests/unit/test_dab_informed_packs.py``: a rule may encode
FORM but never CONTENT. "Report the code column" is legitimate; the code itself is
not. Several rules here are derived from scorer feedback messages, which sometimes
quote ground-truth values — so the boundary is enforced by a mechanical grep against
ground truth, not by author judgement.

Design: docs/superpowers/specs/2026-07-31-benchmark-informed-packs-design.md
"""

from __future__ import annotations


def answer_shape_lines() -> list[str]:
    """Pack A — how the answer should be shaped.

    v1 targeted three measured failures: an entity reported by human-readable name
    where the coded form was expected; a per-group result collapsed to a single
    winner; and a ranked list emitted out of order.

    v2 (2026-08-03, from the Opus packs-ON run vs the archived packs-OFF control):
    rule 1's v1 wording ("give the name alongside it") made the model interpose the
    secondary identifier BETWEEN a label and its value ("Name (code) 5.0"), which
    pushed the value outside a validator's name->score adjacency window and turned
    googlelocal:2 from 3/5 to 0/5 — the delivery side-effect is now forbidden
    explicitly, and the rule defers to the question's requested form instead of
    defaulting to the code (the default-to-code reading was the v1 shape of the
    pancancer query2 near-miss an Opus review caught). Rule 4 is new: googlelocal:3
    fell 3/5 -> 0/5 because a stored weekly schedule was delivered as a summary
    span; the byte-verbatim lever covers single cells but nothing covered
    multi-element stored structures.
    """
    return [
        "When a table offers both a coded identifier and a human-readable name for the "
        "same entity, work out which form the question asks for and deliver that form "
        "immediately followed by the value the question asks about. Add the second "
        "form only after that value — never between a label and its value.",
        "When the result is per-group — per category, per type, per code, per period — "
        "emit EVERY eligible group, not just the leading one.",
        "Present items in the order the question asks for. If it asks for a ranking, "
        "emit them in rank order and keep that order in the final answer line.",
        "When the requested value is itself a stored structure — a schedule, a list, a "
        "mapping — reproduce the whole structure entry by entry as stored, with every "
        "entry kept, even one that records absence or emptiness. Never shorten it to "
        "a digest or an abridged span, and carry the whole structure into the final "
        "answer next to its item — a structure shown only in earlier notes is not "
        "delivered.",
    ]


def validator_shape_lines() -> list[str]:
    """Pack B — where in the text the answer must sit.

    Originally three rules. Two were dropped 2026-07-31 after a whole-branch review
    found they duplicated text the model already receives on the claude-mcp driver:
    adjacency was already covered by `_dab_lever_lines()` lever 7 in ``suite.py``
    ("keep the value within a few characters of its label"), and lead-with-the-answer
    was already covered by ``_build_claude_mcp_prompt``'s own closing line ("Begin
    your reply with the answer in the first sentence... restate the final answer on
    the last line"). A pack rule that restates existing prompt text wastes budget and
    makes its ablation arm uninterpretable, so only the genuinely new rule remains:
    the full-precision/rounded pairing for a value the agent computed itself, scoped
    to not overlap lever 9's byte-verbatim rule for values copied from a cell.
    """
    return [
        "For a numeric answer you computed yourself, give the full-precision value and "
        "a rounded form side by side, so either can be read. This does not apply to a "
        "value copied from a cell — reproduce those exactly as stored.",
    ]


def analytical_convention_lines() -> list[str]:
    """Pack C — recurring analytical choices, stated once so they stop being re-chosen.

    These are the decisions our trials made differently on every attempt: how to seed a
    smoothed average, whether empty periods count, how to treat dirty join keys, and how
    to pull values out of prose without an O(n^2) join.
    """
    return [
        "For a smoothed or moving mean, seed on the first observation and include "
        "every period from the first to the last, counting periods with no data as "
        "zero rather than skipping them.",
        "Normalize obviously corrupted join keys before joining — stray leading "
        "punctuation, trailing whitespace, differing case — and verify the match "
        "rate before trusting the result.",
        "When a value you need is embedded in a free-form text field, extract it ONCE "
        "into a temp table using word-boundary matching, then join on that temp table. "
        "Never join two large tables on a substring test. This is for pulling a known "
        "value out of prose — it is not a way to decide which rows are eligible.",
        "An identifier may be a composite built from several parts joined by a "
        "separator. Keep the whole composite string as the identifier and reproduce "
        "it in full; do not report only its leading part.",
    ]


_PER_DATASET: dict[str, tuple[str, ...]] = {
    "github_repos": (
        "For a question about a named file, the population is the rows where that "
        "file was actually sampled into the table — not every repository.",
    ),
    "pancancer_atlas": (
        "This dataset carries both a coded morphology identifier column and a "
        "free-text type label for the same concept; check which one the question is "
        "asking about.",
    ),
    "deps_dev_v1": (
        "Dependency identifiers here are composite paths joined by a separator. "
        "Reproduce the identifier exactly as stored, separators included.",
    ),
    "agnews": (
        "There is no stored category column; the category must be derived from the "
        "article text itself. Classify rather than keyword-match, and validate the "
        "classifier on a hand-checked sample before trusting its counts.",
    ),
}


def per_dataset_lines(dataset: str) -> list[str]:
    """Pack D — rules naming a dataset and its structural quirks.

    Deliberately narrow: each rule states how the data is SHAPED, never what the
    answer is. This is the pack most likely to be dropped after ablation — a
    competitor running this style scores 0.6137, so it is not a guaranteed win.
    """
    return list(_PER_DATASET.get(dataset, ()))


def all_pack_lines() -> list[str]:
    """Every line from every pack — the surface the contamination gate scans.

    Iterates ``_PER_DATASET`` directly (not ``_DATASETS``): ``suite.py`` reads Pack D
    content via ``per_dataset_lines()``, which resolves against ``_PER_DATASET``. A
    key present there but absent from ``_DATASETS`` (a typo, a renamed dataset) would
    otherwise be emitted into the prompt while never being scanned by this gate.
    """
    lines = answer_shape_lines() + validator_shape_lines() + analytical_convention_lines()
    for dataset in _PER_DATASET:
        lines += per_dataset_lines(dataset)
    return lines


_DATASETS = (
    "agnews",
    "bookreview",
    "crmarenapro",
    "deps_dev_v1",
    "github_repos",
    "googlelocal",
    "music_brainz_20k",
    "pancancer_atlas",
    "patents",
    "stockindex",
    "stockmarket",
    "yelp",
)
