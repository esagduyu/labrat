"""Query history search functions (M28)."""

from __future__ import annotations

import re

from labrat.history.events import QueryEvent


def recent_queries(events: list[QueryEvent], limit: int = 10) -> list[QueryEvent]:
    """Return the most recent queries (last-first by list order)."""
    return list(reversed(events))[:limit]


def find_queries_touching(events: list[QueryEvent], table: str) -> list[QueryEvent]:
    """Return queries that reference the given table name."""
    pattern = re.compile(r"\b" + re.escape(table) + r"\b", re.IGNORECASE)
    return [e for e in events if pattern.search(e.sql_final)]


def find_similar_queries(
    events: list[QueryEvent],
    sql: str,
    limit: int = 10,
) -> list[QueryEvent]:
    """Return queries ranked by keyword overlap with the given SQL.

    Uses a simple bag-of-words approach: splits both SQL strings into tokens
    and scores by intersection size. Good enough for short history lists.
    """

    def _tokens(s: str) -> set[str]:
        return {w.upper() for w in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", s)}

    query_tokens = _tokens(sql)
    scored: list[tuple[int, QueryEvent]] = []
    for event in events:
        overlap = len(query_tokens & _tokens(event.sql_final))
        if overlap > 0:
            scored.append((overlap, event))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [e for _, e in scored[:limit]]
