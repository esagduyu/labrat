"""Extract durable memories from SQL edit diffs and chat corrections (M31).

LLM calls are injected via llm_fn so tests can run without a real API key.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

import sqlglot
from sqlglot import exp

from labrat.history.events import QueryEvent
from labrat.memory.model import Memory, MemoryKind, MemoryScope

LLMFn = Callable[[str], Awaitable[str]]


def resolve_table_scope(sql: str, known_tables: Sequence[str]) -> str | None:
    """Best-effort single-table attribution for a correction's context SQL.

    Returns the one known table the SQL references, or None when zero or
    several match (a multi-table correction gets no table_scope rather than a
    wrong one — cluster_corrections then routes it to __global__).
    """
    known = {t.lower(): t for t in known_tables}
    try:
        root = sqlglot.parse_one(sql)
    except Exception:
        return None
    referenced = {t.name.lower() for t in root.find_all(exp.Table)}
    hits = [known[name] for name in referenced if name in known]
    return hits[0] if len(hits) == 1 else None


class EditExtractor:
    """Derive memories from user edits captured in QueryEvent.edit_diff."""

    def __init__(
        self, profile: str, llm_fn: LLMFn, known_tables: Sequence[str] | None = None
    ) -> None:
        self._profile = profile
        self._llm_fn = llm_fn
        self._known_tables = list(known_tables) if known_tables else []

    async def extract(self, event: QueryEvent) -> list[Memory]:
        if not event.edit_diff:
            return []
        prompt = (
            "A user edited a SQL query. Extract a concise, reusable rule that captures "
            "the intent of their correction. Reply with one sentence only, or an empty "
            "string if no general rule can be inferred.\n\n"
            f"Diff:\n{event.edit_diff}\n\n"
            f"Final SQL:\n{event.sql_final}"
        )
        text = (await self._llm_fn(prompt)).strip()
        if not text:
            return []
        return [
            Memory(
                profile=self._profile,
                scope=MemoryScope.global_,
                kind=MemoryKind.edit_derived,
                text=text,
                source=event.version_id,
                table_scope=(
                    resolve_table_scope(event.sql_final, self._known_tables)
                    if self._known_tables
                    else None
                ),
            )
        ]


class ChatCorrectionExtractor:
    """Derive memories from natural-language corrections in chat."""

    def __init__(
        self, profile: str, llm_fn: LLMFn, known_tables: Sequence[str] | None = None
    ) -> None:
        self._profile = profile
        self._llm_fn = llm_fn
        self._known_tables = list(known_tables) if known_tables else []

    async def extract(self, user_message: str, context_sql: str) -> list[Memory]:
        prompt = (
            "A user corrected a data agent during a conversation. If the message "
            "contains a general rule worth remembering, return it as one concise "
            "sentence. Otherwise return an empty string.\n\n"
            f"User message: {user_message}\n"
            f"Context SQL:\n{context_sql}"
        )
        text = (await self._llm_fn(prompt)).strip()
        if not text:
            return []
        return [
            Memory(
                profile=self._profile,
                scope=MemoryScope.global_,
                kind=MemoryKind.chat_correction,
                text=text,
                table_scope=(
                    resolve_table_scope(context_sql, self._known_tables)
                    if self._known_tables
                    else None
                ),
            )
        ]
