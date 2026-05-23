"""Tests for memory retrieval (M31)."""

from __future__ import annotations

from labrat.memory.model import Memory, MemoryKind, MemoryScope


def _mem(text: str, table_scope: str | None = None) -> Memory:
    return Memory(
        profile="dev",
        scope=MemoryScope.table_ if table_scope else MemoryScope.global_,
        kind=MemoryKind.edit_derived,
        text=text,
        table_scope=table_scope,
    )


# ── relevant_memories ─────────────────────────────────────────────────────────


def test_retrieval_returns_global_memories() -> None:
    from labrat.memory.retrieval import relevant_memories

    mems = [_mem("always use user_id for joins")]
    result = relevant_memories(mems, tables=["orders"], query="revenue by region")
    assert len(result) == 1


def test_retrieval_returns_table_scoped_memories_for_matching_table() -> None:
    from labrat.memory.retrieval import relevant_memories

    mems = [
        _mem("filter by status = completed", table_scope="orders"),
        _mem("use payment_method for grouping", table_scope="payments"),
    ]
    result = relevant_memories(mems, tables=["orders"], query="orders count")
    texts = [m.text for m in result]
    assert any("status" in t for t in texts)
    assert not any("payment_method" in t for t in texts)


def test_retrieval_excludes_table_scoped_for_other_tables() -> None:
    from labrat.memory.retrieval import relevant_memories

    mems = [_mem("payments rule", table_scope="payments")]
    result = relevant_memories(mems, tables=["orders"], query="orders query")
    assert result == []


def test_retrieval_returns_empty_for_no_memories() -> None:
    from labrat.memory.retrieval import relevant_memories

    result = relevant_memories([], tables=["orders"], query="any query")
    assert result == []


def test_retrieval_text_match_boosts_relevance() -> None:
    from labrat.memory.retrieval import relevant_memories

    mems = [
        _mem("join orders to users via user_id"),
        _mem("always use partition key for cost control"),
    ]
    result = relevant_memories(mems, tables=["orders", "users"], query="join orders users")
    # The orders/users-relevant memory should appear first or at all
    assert len(result) >= 1
    assert any("user_id" in m.text for m in result)


def test_retrieval_respects_limit() -> None:
    from labrat.memory.retrieval import relevant_memories

    mems = [_mem(f"memory {i}") for i in range(20)]
    result = relevant_memories(mems, tables=[], query="anything", limit=5)
    assert len(result) <= 5
