# tests/unit/test_maze_provenance.py
from __future__ import annotations

from labrat.maze.provenance import SOURCE_TIERS, best_source, source_rank


def test_ladder_order() -> None:
    assert SOURCE_TIERS[0] == "semantic_layer"
    assert SOURCE_TIERS[-1] == "human"
    assert source_rank("lineage") < source_rank("verified") < source_rank("harvested")
    assert source_rank("harvested") < source_rank("human")


def test_unknown_source_is_lowest() -> None:
    assert source_rank("bogus") == len(SOURCE_TIERS)


def test_best_source_picks_highest_tier() -> None:
    assert best_source(["human", "harvested", "lineage"]) == "lineage"
    assert best_source([]) == "human"
