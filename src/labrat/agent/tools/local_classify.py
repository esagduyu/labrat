"""Local embedding-based classification backend for ``llm_classify``.

Classifies rows with ZERO LLM tokens: row texts and label prototypes are embedded
via the ``semantic`` extra's static embedder (the same model2vec dependency the
hybrid-retrieval feature uses) and each row takes the argmax-cosine label.

Trade-off (recorded in docs/superpowers/specs/2026-07-18-lever-pack-v2-design.md
§D2): weaker than a dedicated zero-shot NLI model on nuanced labels, but ~30MB
and dependency-free beyond an extra the project already ships, deterministic
given fixed model weights, and immune to provider quotas/rate limits. The
``llm_classify_backend`` seam allows a heavier backend later.
"""

from __future__ import annotations

import math
from typing import Any

import polars as pl

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.llm_primitives import ExtractResult, select_classify_rows
from labrat.maze.embedding import get_default_embedder

NO_EMBEDDER_ERROR = (
    "llm_classify backend 'local-embed' requires the optional `semantic` extra "
    "(local embedding model). Install it (`uv sync --extra semantic`) or switch "
    "llm_classify_backend back to 'llm'."
)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


async def classify_rows_local_embed(
    ctx: ToolContext,
    *,
    table: str,
    text_column: str,
    key_columns: list[str],
    labels: list[str],
    where: str | None,
    limit: int | None,
    max_rows: int,
) -> ExtractResult:
    """Classify up to ``max_rows`` rows into ``labels`` by embedding similarity.

    Returns the same ``ExtractResult`` shape as the LLM fan-out path (a `category`
    column constrained to the labels; unclassifiable rows keep NULL and count in
    ``rows_failed``). ``requests_made`` is always 0 — no model API calls occur.
    Raises ``RuntimeError`` when the embedder is unavailable (the tool converts
    this to its structured self-error).
    """
    if not labels:
        raise ValueError("labels must be a non-empty list")
    embedder = get_default_embedder()
    if embedder is None:
        raise RuntimeError(NO_EMBEDDER_ERROR)

    source = select_classify_rows(
        ctx,
        table=table,
        text_column=text_column,
        key_columns=key_columns,
        where=where,
        limit=limit,
        cap=max_rows,
    )
    rows = list(source.iter_rows(named=True))
    label_vectors = embedder.embed(list(labels))

    categories: list[str | None] = []
    rows_failed = 0
    texts = ["" if row.get(text_column) is None else str(row[text_column]) for row in rows]
    row_vectors = embedder.embed(texts) if texts else []
    for text, vector in zip(texts, row_vectors, strict=True):
        if not text.strip():
            categories.append(None)
            rows_failed += 1
            continue
        scores = [_cosine(vector, label_vec) for label_vec in label_vectors]
        best = max(range(len(labels)), key=lambda i: (scores[i], -i))
        if scores[best] == 0.0:
            categories.append(None)
            rows_failed += 1
        else:
            categories.append(labels[best])

    data: dict[str, list[Any]] = {col: [row[col] for row in rows] for col in key_columns}
    data[text_column] = texts
    data["category"] = categories
    df = pl.DataFrame(data)
    return ExtractResult(
        df=df,
        rows_processed=len(rows),
        rows_failed=rows_failed,
        requests_made=0,
    )
