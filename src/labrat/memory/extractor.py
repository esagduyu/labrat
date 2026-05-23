"""Extract durable memories from SQL edit diffs and chat corrections (M31).

LLM calls are injected via llm_fn so tests can run without a real API key.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from labrat.history.events import QueryEvent
from labrat.memory.model import Memory, MemoryKind, MemoryScope

LLMFn = Callable[[str], Awaitable[str]]


class EditExtractor:
    """Derive memories from user edits captured in QueryEvent.edit_diff."""

    def __init__(self, profile: str, llm_fn: LLMFn) -> None:
        self._profile = profile
        self._llm_fn = llm_fn

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
            )
        ]


class ChatCorrectionExtractor:
    """Derive memories from natural-language corrections in chat."""

    def __init__(self, profile: str, llm_fn: LLMFn) -> None:
        self._profile = profile
        self._llm_fn = llm_fn

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
            )
        ]
