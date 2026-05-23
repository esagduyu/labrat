"""LLM-driven context analyzer (M29).

Generates per-table descriptions and common idioms from query history.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from labrat.history.events import QueryEvent

LLMFn = Callable[[str], Awaitable[str]]

_DESCRIBE_PROMPT = (
    "You are a SQL expert. Given the following SQL queries that use the table `{table}`, "
    "write a one-sentence description of what the table likely contains and how it is "
    "typically used. Be concise and specific.\n\n"
    "Queries:\n{queries}\n\n"
    "Description:"
)


class ContextAnalyzer:
    """Generates table descriptions and usage patterns from query history."""

    def __init__(
        self,
        model: str = "claude-haiku-4-5-20251001",
        llm_fn: LLMFn | None = None,
    ) -> None:
        self._model = model
        self._llm_fn = llm_fn or self._default_llm

    async def _default_llm(self, prompt: str) -> str:
        import anthropic  # type: ignore[import-untyped]

        client = anthropic.AsyncAnthropic()
        msg = await client.messages.create(  # pyright: ignore[reportUnknownMemberType]
            model=self._model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        return str(msg.content[0].text)  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType,reportUnknownArgumentType]

    async def generate_descriptions(
        self,
        tables: list[str],
        events: list[QueryEvent],
        max_queries_per_table: int = 5,
    ) -> dict[str, str]:
        """Return a description for each table, generated from relevant queries."""
        import re

        descriptions: dict[str, str] = {}
        for table in tables:
            pattern = re.compile(r"\b" + re.escape(table) + r"\b", re.IGNORECASE)
            relevant = [e.sql_final for e in events if pattern.search(e.sql_final)]
            if not relevant:
                descriptions[table] = f"{table} — no query history available."
                continue
            sample = "\n".join(relevant[:max_queries_per_table])
            prompt = _DESCRIBE_PROMPT.format(table=table, queries=sample)
            desc = await self._llm_fn(prompt)
            descriptions[table] = desc.strip()
        return descriptions
