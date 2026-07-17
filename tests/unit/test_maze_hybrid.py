"""Tests for RRF fusion math and the hybrid section re-rank (T2b v2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from labrat.maze.embedding import SectionEmbeddingCache
from labrat.maze.hybrid import fused_section_order, rrf_fuse, semantic_ranking

K1 = ("stockindex", 0)
K2 = ("stockindex", 1)
K3 = ("revenue", 0)


def test_rrf_fuse_matches_hand_computed_values() -> None:
    fused = rrf_fuse([[K1, K2], [K2, K1]], k=60)
    # K1: 1/61 (rank 0) + 1/62 (rank 1); K2 symmetric — equal scores.
    assert fused[K1] == pytest.approx(1 / 61 + 1 / 62)
    assert fused[K2] == pytest.approx(fused[K1])


def test_rrf_fuse_key_absent_from_one_ranking_gets_partial_score() -> None:
    fused = rrf_fuse([[K1], [K1, K3]], k=60)
    assert fused[K1] == pytest.approx(1 / 61 + 1 / 61)
    assert fused[K3] == pytest.approx(1 / 62)


def test_semantic_ranking_sorts_by_cosine_with_stable_tiebreak() -> None:
    q = [1.0, 0.0]
    cands = [(K2, [1.0, 0.0]), (K1, [1.0, 0.0]), (K3, [0.0, 1.0])]
    order = semantic_ranking(q, cands)
    assert order == [K1, K2, K3]  # ties broken by key, orthogonal vector last


class _KeywordEmbedder:
    """Fake semantic space: 'revenue' and 'profits' project onto the same axis."""

    @property
    def model_id(self) -> str:
        return "kw-stub"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [
                float(("revenue" in t.lower()) or ("profit" in t.lower())),
                float("churn" in t.lower()),
                1.0,
            ]
            for t in texts
        ]


def test_fused_order_surfaces_lexically_disjoint_section(tmp_path: Path) -> None:
    candidates = [
        (K3, "Revenue trends\nMonthly revenue rollups live in fct_revenue."),
        (K1, "Index dates\nThe Date column is dirty mixed-format text."),
    ]
    lexical_order = [K1]  # the lexical arm never matched the revenue section
    cache = SectionEmbeddingCache(tmp_path / ".embeddings.jsonl", _KeywordEmbedder())
    order = fused_section_order(
        "chart of profits by month",
        candidates,
        lexical_order,
        embedder=_KeywordEmbedder(),
        cache=cache,
    )
    assert order is not None
    # K3 never lexically matched, yet the semantic arm surfaces it in the fused
    # order (retrievable within top-k). K1 correctly stays first: it appears in
    # BOTH rankings (lexical rank 0 + semantic rank 1 > semantic rank 0 alone).
    assert order == [K1, K3]


def test_fused_order_is_deterministic(tmp_path: Path) -> None:
    candidates = [(K1, "alpha beta"), (K2, "gamma delta"), (K3, "revenue profits")]
    cache = SectionEmbeddingCache(tmp_path / ".embeddings.jsonl", _KeywordEmbedder())
    runs = [
        fused_section_order("profits", candidates, [K2], embedder=_KeywordEmbedder(), cache=cache)
        for _ in range(3)
    ]
    assert runs[0] == runs[1] == runs[2]


def test_fused_order_fails_open_without_embedder(tmp_path: Path) -> None:
    assert fused_section_order("q", [(K1, "text")], [K1], embedder=None, cache=None) is None
