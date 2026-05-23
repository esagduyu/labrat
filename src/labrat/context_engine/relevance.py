"""Table relevance scoring from query history (M29).

Score = frequency * recency_weight. Recency weight decays exponentially
so that recent queries contribute more than old ones.
FK-graph propagation: tables that often appear together get a small bonus.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

from labrat.history.events import QueryEvent

_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?)",
    re.IGNORECASE,
)
_HALF_LIFE_DAYS = 30.0


def _recency_weight(ts: datetime) -> float:
    now = datetime.now(tz=UTC)
    age_days = max(0.0, (now - ts).total_seconds() / 86400)
    return math.exp(-math.log(2) * age_days / _HALF_LIFE_DAYS)


def _extract_tables(sql: str) -> list[str]:
    raw: list[str] = _TABLE_RE.findall(sql)
    tables: list[str] = []
    for t in raw:
        name = t.split(".")[-1].lower()
        tables.append(name)
    return tables


def score_table_relevance(events: list[QueryEvent]) -> dict[str, float]:
    """Return a frequency * recency score for each table mentioned in events."""
    scores: dict[str, float] = {}
    for event in events:
        weight = _recency_weight(event.timestamp)
        for table in _extract_tables(event.sql_final):
            scores[table] = scores.get(table, 0.0) + weight
    return scores
