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

    Targets three measured failures: an entity reported by human-readable name where
    the coded form was expected; a per-group result collapsed to a single winner; and
    a ranked list emitted out of order.
    """
    return [
        "When a table offers both a coded identifier and a human-readable name for the "
        "same entity, report the coded form, unless the question explicitly asks for "
        "the name. Give the name alongside it only as secondary context.",
        "When the result is per-group — per category, per type, per code, per period — "
        "emit EVERY group you computed, not just the leading one. However many groups "
        "you computed, that many groups appear in your answer.",
        "Present items in the order the question asks for. If it asks for a ranking, "
        "emit them in rank order and keep that order in the final answer line.",
    ]


def validator_shape_lines() -> list[str]:
    """Pack B — where in the text the answer must sit.

    Scoring reads position, not just content: a correct value placed too far from its
    label, or too late in the reply, has repeatedly scored zero.
    """
    return [
        "Put each value immediately after the thing it describes, with nothing in "
        "between — write the label then the number, not the label followed by a phrase "
        "and then the number.",
        "State the answer in the very first sentence of your reply, then show your "
        "work, then restate the answer on the last line. Do not open with methodology.",
        "For a numeric answer give the full-precision value and a rounded form "
        "side by side, so either can be read.",
    ]


def analytical_convention_lines() -> list[str]:
    return []


def per_dataset_lines(dataset: str) -> list[str]:
    return []


def all_pack_lines() -> list[str]:
    """Every line from every pack — the surface the contamination gate scans."""
    lines = answer_shape_lines() + validator_shape_lines() + analytical_convention_lines()
    for dataset in _DATASETS:
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
