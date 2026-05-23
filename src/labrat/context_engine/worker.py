"""Background worker that rebuilds the ContextBundle (M29).

Triggers:
- Every N queries (default 10) during a session
- Manually via `labrat context rebuild`
- On profile activation if bundle is stale (>7 days)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from labrat.context_engine.analyzer import ContextAnalyzer
from labrat.context_engine.bundle import ContextBundle, TableContext
from labrat.context_engine.relevance import score_table_relevance
from labrat.history.events import QueryEvent

_STALE_DAYS = 7
_DEFAULT_TOP_K = 10
_DEFAULT_REBUILD_EVERY = 10


class ContextWorker:
    """Manages ContextBundle lifecycle for a single profile.

    Call `maybe_rebuild(events)` after each query — it rebuilds when the
    query count crosses the rebuild threshold.
    """

    def __init__(
        self,
        profile: str,
        context_dir: Path | None = None,
        rebuild_every: int = _DEFAULT_REBUILD_EVERY,
        top_k: int = _DEFAULT_TOP_K,
        model: str = "claude-haiku-4-5-20251001",
    ) -> None:
        self._profile = profile
        self._context_dir = context_dir
        self._rebuild_every = rebuild_every
        self._top_k = top_k
        self._analyzer = ContextAnalyzer(model=model)
        self._query_count = 0

    def is_stale(self) -> bool:
        bundle = ContextBundle.load(self._profile, self._context_dir)
        if bundle is None:
            return True
        age = datetime.now(tz=UTC) - bundle.generated_at
        return age > timedelta(days=_STALE_DAYS)

    async def maybe_rebuild(self, events: list[QueryEvent]) -> ContextBundle | None:
        """Rebuild the bundle if the query count threshold has been crossed."""
        self._query_count += 1
        if self._query_count % self._rebuild_every == 0 or self.is_stale():
            return await self.rebuild(events)
        return None

    async def rebuild(self, events: list[QueryEvent]) -> ContextBundle:
        """Rebuild and persist the ContextBundle from the given events."""
        scores = score_table_relevance(events)
        top_tables = sorted(scores, key=lambda t: scores[t], reverse=True)[: self._top_k]

        descriptions = await self._analyzer.generate_descriptions(
            tables=top_tables,
            events=events,
        )

        table_contexts: list[TableContext] = []
        for table in top_tables:
            table_contexts.append(
                TableContext(
                    table_name=table,
                    description=descriptions.get(table, ""),
                )
            )

        bundle = ContextBundle(
            profile=self._profile,
            generated_at=datetime.now(tz=UTC),
            tables=table_contexts,
        )
        bundle.save(self._context_dir)
        return bundle
