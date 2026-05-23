"""Tests for the context engine (M29)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from labrat.context_engine.bundle import ContextBundle, TableContext
from labrat.context_engine.relevance import score_table_relevance
from labrat.history.events import QueryEvent

# ── helpers ───────────────────────────────────────────────────────────────────


def _event(sql: str, ts: datetime | None = None) -> QueryEvent:
    return QueryEvent(
        timestamp=ts or datetime.now(tz=UTC),
        profile="dev",
        thread_id="t1",
        version_id="v1",
        sql_final=sql,
        executed=True,
        success=True,
    )


# ── relevance scoring ─────────────────────────────────────────────────────────


def test_relevance_scores_higher_for_frequent_tables() -> None:
    events = [_event("SELECT * FROM orders") for _ in range(5)] + [_event("SELECT * FROM users")]
    scores = score_table_relevance(events)
    assert scores["orders"] > scores["users"]


def test_relevance_returns_empty_for_no_events() -> None:
    scores = score_table_relevance([])
    assert scores == {}


def test_relevance_detects_table_from_join() -> None:
    events = [_event("SELECT * FROM orders o JOIN users u ON o.user_id = u.id")]
    scores = score_table_relevance(events)
    assert "orders" in scores
    assert "users" in scores


def test_relevance_returns_all_tables_mentioned() -> None:
    events = [
        _event("SELECT * FROM orders"),
        _event("SELECT * FROM payments"),
        _event("SELECT * FROM orders JOIN payments ON orders.id = payments.order_id"),
    ]
    scores = score_table_relevance(events)
    assert "orders" in scores
    assert "payments" in scores


def test_relevance_higher_recency_scores_higher() -> None:

    old = datetime(2020, 1, 1, tzinfo=UTC)
    recent = datetime.now(tz=UTC)
    events = [
        _event("SELECT * FROM old_table", ts=old),
        _event("SELECT * FROM new_table", ts=recent),
    ]
    scores = score_table_relevance(events)
    assert scores.get("new_table", 0) > scores.get("old_table", 0)


# ── ContextBundle ─────────────────────────────────────────────────────────────


def test_context_bundle_fields() -> None:
    bundle = ContextBundle(
        profile="dev",
        generated_at=datetime.now(tz=UTC),
        tables=[
            TableContext(
                table_name="orders",
                schema_name="public",
                description="Contains transactional order data.",
                frequent_columns=["status", "user_id", "total_amount"],
                common_filters=["status = 'active'"],
                common_joins=["users ON orders.user_id = users.id"],
            )
        ],
        idioms=["Always filter orders by status", "Join orders to users via user_id"],
    )
    assert bundle.profile == "dev"
    assert len(bundle.tables) == 1
    assert bundle.tables[0].table_name == "orders"


def test_context_bundle_external_catalog_slots() -> None:
    bundle = ContextBundle(
        profile="dev",
        generated_at=datetime.now(tz=UTC),
    )
    assert bundle.dbt_models is None
    assert bundle.mcp_descriptions is None


def test_context_bundle_serialization_roundtrip(tmp_path: Path) -> None:
    bundle = ContextBundle(
        profile="dev",
        generated_at=datetime.now(tz=UTC),
        tables=[
            TableContext(
                table_name="orders",
                schema_name="public",
                description="Order table",
            )
        ],
    )
    path = tmp_path / "bundle.json"
    path.write_text(bundle.model_dump_json())
    restored = ContextBundle.model_validate_json(path.read_text())
    assert restored.profile == "dev"
    assert restored.tables[0].table_name == "orders"


def test_context_bundle_to_prompt_snippet() -> None:
    bundle = ContextBundle(
        profile="dev",
        generated_at=datetime.now(tz=UTC),
        tables=[
            TableContext(
                table_name="orders",
                schema_name="public",
                description="Contains transactional order data.",
                frequent_columns=["status", "user_id"],
            )
        ],
        idioms=["Always filter by status"],
    )
    snippet = bundle.to_prompt_snippet()
    assert "orders" in snippet
    assert "status" in snippet


# ── LLM-mocked analyzer ───────────────────────────────────────────────────────


_RUN_LLM = os.environ.get("LABRAT_RUN_LLM_TESTS")


@pytest.mark.skipif(not _RUN_LLM, reason="Set LABRAT_RUN_LLM_TESTS=1 to run LLM tests")
async def test_analyzer_generates_table_description() -> None:
    from labrat.context_engine.analyzer import ContextAnalyzer

    events = [_event("SELECT status, COUNT(*) FROM orders GROUP BY status")]
    analyzer = ContextAnalyzer(model="claude-haiku-4-5-20251001")
    descriptions = await analyzer.generate_descriptions(
        tables=["orders"],
        events=events,
    )
    assert "orders" in descriptions
    assert len(descriptions["orders"]) > 10


@pytest.mark.asyncio
async def test_analyzer_mocked_returns_descriptions() -> None:
    """Analyzer with mocked LLM returns expected structure."""
    from labrat.context_engine.analyzer import ContextAnalyzer

    async def _mock_llm(prompt: str) -> str:
        return "orders contains transactional revenue data."

    events = [_event("SELECT * FROM orders")]
    analyzer = ContextAnalyzer(model="stub", llm_fn=_mock_llm)
    descriptions = await analyzer.generate_descriptions(tables=["orders"], events=events)
    assert "orders" in descriptions
    assert "orders" in descriptions["orders"]
