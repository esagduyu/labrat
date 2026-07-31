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
    return []


def validator_shape_lines() -> list[str]:
    return []


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
