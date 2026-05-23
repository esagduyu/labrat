"""Tests for memory extractor (M31) — LLM is mocked."""

from __future__ import annotations

import pytest

from labrat.history.events import QueryEvent
from labrat.memory.model import MemoryKind


def _event(
    sql_final: str, sql_initial: str | None = None, edit_diff: str | None = None
) -> QueryEvent:
    from datetime import UTC, datetime

    return QueryEvent(
        timestamp=datetime.now(tz=UTC),
        profile="dev",
        thread_id="t1",
        version_id="v1",
        sql_final=sql_final,
        sql_initial=sql_initial,
        edit_diff=edit_diff,
        executed=True,
        success=True,
    )


# ── EditExtractor ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_edit_extractor_returns_memory_from_diff() -> None:
    from labrat.memory.extractor import EditExtractor

    diff = "-  JOIN customers c ON o.customer_id = c.id\n+  JOIN users u ON o.user_id = u.id"
    event = _event(
        sql_final="SELECT * FROM orders o JOIN users u ON o.user_id = u.id",
        sql_initial="SELECT * FROM orders o JOIN customers c ON o.customer_id = c.id",
        edit_diff=diff,
    )

    async def _mock_llm(prompt: str) -> str:
        return "always join orders to users via user_id, not customer_id"

    extractor = EditExtractor(profile="dev", llm_fn=_mock_llm)
    memories = await extractor.extract(event)
    assert len(memories) == 1
    assert memories[0].kind == MemoryKind.edit_derived
    assert "user_id" in memories[0].text


@pytest.mark.asyncio
async def test_edit_extractor_skips_event_without_diff() -> None:
    from labrat.memory.extractor import EditExtractor

    event = _event(sql_final="SELECT 1")

    async def _mock_llm(prompt: str) -> str:
        return "should not be called"

    extractor = EditExtractor(profile="dev", llm_fn=_mock_llm)
    memories = await extractor.extract(event)
    assert memories == []


@pytest.mark.asyncio
async def test_edit_extractor_returns_empty_on_empty_llm_response() -> None:
    from labrat.memory.extractor import EditExtractor

    diff = "-old\n+new"
    event = _event(sql_final="SELECT 1", sql_initial="SELECT 0", edit_diff=diff)

    async def _mock_llm(prompt: str) -> str:
        return ""

    extractor = EditExtractor(profile="dev", llm_fn=_mock_llm)
    memories = await extractor.extract(event)
    assert memories == []


# ── ChatCorrectionExtractor ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_extractor_returns_memory_from_correction() -> None:
    from labrat.memory.extractor import ChatCorrectionExtractor

    async def _mock_llm(prompt: str) -> str:
        return "always filter orders by status = 'completed'"

    extractor = ChatCorrectionExtractor(profile="dev", llm_fn=_mock_llm)
    memories = await extractor.extract(
        user_message="no, always filter by status = 'completed'",
        context_sql="SELECT * FROM orders",
    )
    assert len(memories) == 1
    assert memories[0].kind == MemoryKind.chat_correction


@pytest.mark.asyncio
async def test_chat_extractor_returns_empty_on_non_correction() -> None:
    from labrat.memory.extractor import ChatCorrectionExtractor

    async def _mock_llm(prompt: str) -> str:
        return ""

    extractor = ChatCorrectionExtractor(profile="dev", llm_fn=_mock_llm)
    memories = await extractor.extract(
        user_message="thanks!",
        context_sql="SELECT * FROM orders",
    )
    assert memories == []
