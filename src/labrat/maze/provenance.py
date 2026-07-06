"""Scent source-tier trust ladder (Anthropic 'provenance footer' ordering).

semantic_layer > lineage > verified > harvested > draft > human. Consumed by the
future T3c provenance footer and any code that must pick the most-trustworthy
source among a doc's sections.
"""

from __future__ import annotations

SOURCE_TIERS: list[str] = [
    "semantic_layer",
    "lineage",
    "verified",
    "harvested",
    "draft",
    "human",
]


def source_rank(source: str) -> int:
    """0 = highest trust; unknown tokens rank lowest."""
    try:
        return SOURCE_TIERS.index(source)
    except ValueError:
        return len(SOURCE_TIERS)


def best_source(sources: list[str]) -> str:
    """The highest-tier source in the list; 'human' if empty."""
    if not sources:
        return "human"
    return min(sources, key=source_rank)
