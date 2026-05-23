"""Tests for Thread/Version/Finding data models (M17)."""

from __future__ import annotations

from datetime import UTC, datetime

from labrat.thread.model import Finding, Thread, Version


def test_thread_creation() -> None:
    """Thread can be created with required fields."""
    t = Thread(
        id="t1",
        name="sales analysis",
        profile_name="prod",
        created_at=datetime.now(tz=UTC),
    )
    assert t.id == "t1"
    assert t.parent_version_id is None


def test_thread_with_parent_version() -> None:
    """Thread can record a parent_version_id for branching."""
    t = Thread(
        id="t2",
        name="branch",
        profile_name="prod",
        created_at=datetime.now(tz=UTC),
        parent_version_id="v1",
    )
    assert t.parent_version_id == "v1"


def test_version_creation() -> None:
    """Version stores SQL and chat history."""
    v = Version(
        id="v1",
        thread_id="t1",
        sql="SELECT 1",
        results_ref=None,
        chat_history=[{"role": "user", "content": "hello"}],
        created_at=datetime.now(tz=UTC),
    )
    assert v.sql == "SELECT 1"
    assert v.results_ref is None
    assert len(v.chat_history) == 1


def test_finding_creation() -> None:
    """Finding records a pinned (question, sql, results) tuple."""
    f = Finding(
        id="f1",
        version_id="v1",
        question="how many orders?",
        sql="SELECT COUNT(*) FROM orders",
        results_ref=None,
        chart_spec=None,
        note="baseline count",
        pinned_at=datetime.now(tz=UTC),
    )
    assert f.question == "how many orders?"
    assert f.chart_spec is None


def test_thread_roundtrips_json() -> None:
    """Thread serializes and deserializes cleanly."""
    t = Thread(
        id="t3",
        name="test",
        profile_name="dev",
        created_at=datetime.now(tz=UTC),
    )
    data = t.model_dump()
    t2 = Thread.model_validate(data)
    assert t2.id == t.id
    assert t2.name == t.name


def test_version_roundtrips_json() -> None:
    """Version serializes and deserializes cleanly."""
    v = Version(
        id="v2",
        thread_id="t1",
        sql="SELECT 2",
        results_ref="/tmp/result.parquet",
        chat_history=[],
        created_at=datetime.now(tz=UTC),
    )
    data = v.model_dump()
    v2 = Version.model_validate(data)
    assert v2.sql == v.sql
    assert v2.results_ref == v.results_ref
