"""Memory retrieval: score and rank memories for a given query context (M31).

Scoring heuristic (no embeddings required):
  - Global memories always score 1.0
  - Table-scoped memories score 2.0 if their table is in the query context, else 0
  - Text overlap with query and table names boosts score by 0.1 per matching token

Returns top-K by score descending.
"""

from __future__ import annotations

from labrat.memory.model import Memory, MemoryScope

_STOP_WORDS = frozenset(
    {"a", "an", "the", "to", "of", "in", "for", "on", "with", "by", "from", "is", "and", "or"}
)


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in text.split() if w.lower() not in _STOP_WORDS}


def _score(memory: Memory, tables: list[str], query: str) -> float:
    if memory.scope == MemoryScope.table_:
        if memory.table_scope not in tables:
            return 0.0
        base = 2.0
    else:
        base = 1.0

    query_tokens = _tokens(query) | {t.lower() for t in tables}
    memory_tokens = _tokens(memory.text)
    overlap = len(query_tokens & memory_tokens)
    return base + 0.1 * overlap


def relevant_memories(
    memories: list[Memory],
    tables: list[str],
    query: str,
    limit: int = 10,
) -> list[Memory]:
    """Return the most relevant memories for the given context, up to *limit*."""
    scored = [(m, _score(m, tables, query)) for m in memories]
    filtered = [(m, s) for m, s in scored if s > 0]
    filtered.sort(key=lambda x: x[1], reverse=True)
    return [m for m, _ in filtered[:limit]]
