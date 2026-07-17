"""Hybrid lexical+semantic retrieval: Reciprocal Rank Fusion over section rankings.

Pure functions plus one orchestrator (``fused_section_order``) shared by
``search_reference_docs`` and ``search_trails``. Everything is deterministic
given fixed inputs; every failure path returns ``None`` so the calling tool
falls back to today's lexical-only behavior (the flag-off / benchmark contract).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path

from labrat.maze.document import ScentDoc
from labrat.maze.embedding import Embedder, SectionEmbeddingCache, get_default_embedder

#: Section identity within one retrieval: (domain, section index).
SectionKey = tuple[str, int]

RRF_K = 60


def rrf_fuse(rankings: Sequence[Sequence[SectionKey]], k: int = RRF_K) -> dict[SectionKey, float]:
    """Standard RRF: score(key) = Σ over rankings of 1/(k + rank) where present."""
    fused: dict[SectionKey, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking):
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + rank + 1)
    return fused


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(
        x * y for x, y in zip(a, b, strict=False)
    )  # fail-open: mismatched dims score what overlaps
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def semantic_ranking(
    query_vec: Sequence[float], candidates: Sequence[tuple[SectionKey, Sequence[float]]]
) -> list[SectionKey]:
    """Rank candidate sections by cosine similarity; ties break by key (stable)."""
    scored = [(-_cosine(query_vec, vec), key) for key, vec in candidates]
    scored.sort()
    return [key for _, key in scored]


def fused_section_order(
    question: str,
    candidates: Sequence[tuple[SectionKey, str]],
    lexical_order: Sequence[SectionKey],
    *,
    embedder: Embedder | None,
    cache: SectionEmbeddingCache | None,
    k: int = RRF_K,
) -> list[SectionKey] | None:
    """Fuse the lexical ranking with a semantic ranking over ALL candidates.

    Returns the fused order (may include sections with zero lexical overlap —
    the point of the semantic arm), or ``None`` to signal fall-back-to-lexical
    (no embedder, no candidates, or any embedding failure).
    """
    if embedder is None or cache is None or not candidates:
        return None
    try:
        section_vecs = cache.get_or_embed([text for _, text in candidates])
        query_vec = embedder.embed([question])[0]
    except Exception:
        return None
    sem = semantic_ranking(
        query_vec, list(zip((key for key, _ in candidates), section_vecs, strict=True))
    )
    fused = rrf_fuse([list(lexical_order), sem], k=k)
    ordered = sorted(fused.items(), key=lambda item: (-item[1], item[0]))
    return [key for key, _ in ordered]


def user_embedding_sidecar(profile: str, kind: str, home: Path | None = None) -> Path:
    """The per-profile, per-kind ``.embeddings.jsonl`` sidecar (user maze layer).

    One sidecar per kind is enough: entries are content-hash keyed, so which
    layer a section came from is irrelevant. The user layer is the always-
    writable location in the TUI flow; the benchmark path never gets here
    (flag off).
    """
    return (home or Path.home()) / ".labrat" / "maze" / profile / kind / ".embeddings.jsonl"


def hybrid_section_keys(
    question: str,
    docs: Sequence[ScentDoc],
    *,
    skip_heading: str,
    lexical_order: Sequence[SectionKey],
    profile: str,
    kind: str,
) -> list[SectionKey] | None:
    """Tool-facing orchestrator: fused order over every non-context section.

    ``docs`` are merged ``ScentDoc``s. Returns ``None`` on any fail-open
    condition so the calling tool keeps its pure-lexical result.
    """
    embedder = get_default_embedder()
    if embedder is None:
        return None
    candidates: list[tuple[SectionKey, str]] = []
    for doc in docs:
        for idx, section in enumerate(doc.sections):
            if section.heading.strip().lower() == skip_heading:
                continue
            candidates.append(
                ((doc.domain, idx), f"{doc.domain} {section.heading}\n{section.body}")
            )
    cache = SectionEmbeddingCache(user_embedding_sidecar(profile, kind), embedder)
    return fused_section_order(question, candidates, lexical_order, embedder=embedder, cache=cache)
