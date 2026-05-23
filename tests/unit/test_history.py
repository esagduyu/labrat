"""Tests for query history capture (M28)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from labrat.history.events import QueryEvent
from labrat.history.log import QueryHistoryLog
from labrat.history.pii import redact_pii
from labrat.history.search import find_queries_touching, find_similar_queries, recent_queries

# ── QueryEvent ────────────────────────────────────────────────────────────────


def test_query_event_fields() -> None:
    event = QueryEvent(
        timestamp=datetime.now(tz=UTC),
        profile="dev",
        thread_id="t1",
        version_id="v1",
        sql_final="SELECT 1",
        executed=True,
        success=True,
        execution_time_ms=42,
        row_count=1,
    )
    assert event.profile == "dev"
    assert event.sql_final == "SELECT 1"
    assert event.executed is True


def test_query_event_optional_fields() -> None:
    event = QueryEvent(
        timestamp=datetime.now(tz=UTC),
        profile="dev",
        thread_id="t1",
        version_id="v1",
        sql_final="SELECT 1",
        executed=False,
    )
    assert event.sql_initial is None
    assert event.edit_diff is None
    assert event.error_message is None
    assert event.row_count is None
    assert event.execution_time_ms is None


def test_query_event_json_roundtrip() -> None:
    event = QueryEvent(
        timestamp=datetime.now(tz=UTC),
        profile="dev",
        thread_id="t1",
        version_id="v1",
        sql_final="SELECT * FROM orders",
        executed=True,
        success=True,
    )
    raw = event.model_dump_json()
    restored = QueryEvent.model_validate_json(raw)
    assert restored.sql_final == event.sql_final


# ── QueryHistoryLog ───────────────────────────────────────────────────────────


def test_history_log_append_and_read(tmp_path: Path) -> None:
    log = QueryHistoryLog(history_dir=tmp_path)
    event = QueryEvent(
        timestamp=datetime.now(tz=UTC),
        profile="dev",
        thread_id="t1",
        version_id="v1",
        sql_final="SELECT COUNT(*) FROM orders",
        executed=True,
        success=True,
    )
    log.append(event)
    events = log.read_profile("dev")
    assert len(events) == 1
    assert events[0].sql_final == "SELECT COUNT(*) FROM orders"


def test_history_log_multiple_profiles(tmp_path: Path) -> None:
    log = QueryHistoryLog(history_dir=tmp_path)
    e1 = QueryEvent(
        timestamp=datetime.now(tz=UTC),
        profile="dev",
        thread_id="t1",
        version_id="v1",
        sql_final="SELECT 1",
        executed=True,
    )
    e2 = QueryEvent(
        timestamp=datetime.now(tz=UTC),
        profile="prod",
        thread_id="t2",
        version_id="v2",
        sql_final="SELECT 2",
        executed=True,
    )
    log.append(e1)
    log.append(e2)
    dev = log.read_profile("dev")
    prod = log.read_profile("prod")
    assert len(dev) == 1
    assert len(prod) == 1


def test_history_log_creates_dir(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "history"
    log = QueryHistoryLog(history_dir=nested)
    event = QueryEvent(
        timestamp=datetime.now(tz=UTC),
        profile="dev",
        thread_id="t",
        version_id="v",
        sql_final="SELECT 1",
        executed=False,
    )
    log.append(event)
    assert (nested / "dev.jsonl").exists()


# ── search ────────────────────────────────────────────────────────────────────


def _make_events() -> list[QueryEvent]:
    now = datetime.now(tz=UTC)
    return [
        QueryEvent(
            timestamp=now,
            profile="dev",
            thread_id="t1",
            version_id="v1",
            sql_final="SELECT COUNT(*) FROM orders WHERE status = 'active'",
            executed=True,
            success=True,
        ),
        QueryEvent(
            timestamp=now,
            profile="dev",
            thread_id="t2",
            version_id="v2",
            sql_final="SELECT user_id, SUM(amount) FROM payments GROUP BY user_id",
            executed=True,
            success=True,
        ),
        QueryEvent(
            timestamp=now,
            profile="dev",
            thread_id="t3",
            version_id="v3",
            sql_final="SELECT * FROM users LIMIT 10",
            executed=True,
            success=True,
        ),
    ]


def test_recent_queries_returns_events() -> None:
    events = _make_events()
    result = recent_queries(events, limit=2)
    assert len(result) == 2


def test_recent_queries_default_limit() -> None:
    events = _make_events()
    result = recent_queries(events)
    assert len(result) == len(events)


def test_find_queries_touching_table() -> None:
    events = _make_events()
    result = find_queries_touching(events, table="orders")
    assert len(result) == 1
    assert "orders" in result[0].sql_final


def test_find_queries_touching_no_match() -> None:
    events = _make_events()
    result = find_queries_touching(events, table="nonexistent")
    assert result == []


def test_find_similar_queries_matches_keyword() -> None:
    events = _make_events()
    result = find_similar_queries(events, sql="SELECT FROM payments")
    assert any("payments" in e.sql_final for e in result)


# ── PII redaction ─────────────────────────────────────────────────────────────


def test_redact_pii_email() -> None:
    sql = "SELECT * FROM users WHERE email = 'alice@example.com'"
    redacted = redact_pii(sql)
    assert "alice@example.com" not in redacted
    assert "[REDACTED_EMAIL]" in redacted


def test_redact_pii_phone() -> None:
    sql = "SELECT * FROM contacts WHERE phone = '555-867-5309'"
    redacted = redact_pii(sql)
    assert "555-867-5309" not in redacted
    assert "[REDACTED_PHONE]" in redacted


def test_redact_pii_ssn() -> None:
    sql = "SELECT * FROM records WHERE ssn = '123-45-6789'"
    redacted = redact_pii(sql)
    assert "123-45-6789" not in redacted
    assert "[REDACTED_SSN]" in redacted


def test_redact_pii_no_pii() -> None:
    sql = "SELECT COUNT(*) FROM orders WHERE status = 'active'"
    redacted = redact_pii(sql)
    assert redacted == sql
