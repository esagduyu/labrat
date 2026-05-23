"""Tests for Audit Log event sourcing (M19)."""

from __future__ import annotations

import json
from pathlib import Path

from labrat.audit.events import (
    AgentMessage,
    ChartCreated,
    ExportRequested,
    FindingPinned,
    ProfileSwitched,
    SqlExecuted,
    ToolCall,
    ToolResult,
    UserPrompt,
    parse_event,
)
from labrat.audit.log import AuditLog

# ── round-trip tests ──────────────────────────────────────────────────────────


def test_user_prompt_roundtrip() -> None:
    e = UserPrompt(session_id="s1", text="how many orders?")
    data = e.model_dump(mode="json")
    e2 = parse_event(data)
    assert isinstance(e2, UserPrompt)
    assert e2.text == e.text


def test_agent_message_roundtrip() -> None:
    e = AgentMessage(session_id="s1", text="I found 3 tables.")
    data = e.model_dump(mode="json")
    assert isinstance(parse_event(data), AgentMessage)


def test_tool_call_roundtrip() -> None:
    e = ToolCall(session_id="s1", tool_name="list_tables", args={"schema_filter": None})
    data = e.model_dump(mode="json")
    e2 = parse_event(data)
    assert isinstance(e2, ToolCall)
    assert e2.tool_name == "list_tables"


def test_tool_result_roundtrip() -> None:
    e = ToolResult(session_id="s1", tool_name="list_tables", ok=True, result_ref="3 tables")
    data = e.model_dump(mode="json")
    assert isinstance(parse_event(data), ToolResult)


def test_sql_executed_roundtrip() -> None:
    e = SqlExecuted(
        session_id="s1",
        sql="SELECT COUNT(*) FROM orders",
        success=True,
        row_count=1,
        results_ref=None,
    )
    data = e.model_dump(mode="json")
    e2 = parse_event(data)
    assert isinstance(e2, SqlExecuted)
    assert e2.sql == e.sql
    # Sensitive data note: results_ref is None — full result set is NOT logged


def test_chart_created_roundtrip() -> None:
    e = ChartCreated(session_id="s1", chart_spec={"type": "bar"}, results_ref="/tmp/r.parquet")
    data = e.model_dump(mode="json")
    assert isinstance(parse_event(data), ChartCreated)


def test_finding_pinned_roundtrip() -> None:
    e = FindingPinned(session_id="s1", finding_id="f1", question="how many?", sql="SELECT 1")
    data = e.model_dump(mode="json")
    assert isinstance(parse_event(data), FindingPinned)


def test_export_requested_roundtrip() -> None:
    e = ExportRequested(session_id="s1", finding_ids=["f1", "f2"], output_path="/tmp/out.html")
    data = e.model_dump(mode="json")
    assert isinstance(parse_event(data), ExportRequested)


def test_profile_switched_roundtrip() -> None:
    e = ProfileSwitched(session_id="s1", old_profile="dev", new_profile="prod")
    data = e.model_dump(mode="json")
    assert isinstance(parse_event(data), ProfileSwitched)


# ── AuditLog tests ────────────────────────────────────────────────────────────


def test_audit_log_writes_jsonl(tmp_path: Path) -> None:
    """AuditLog.log() appends valid JSONL lines."""
    log = AuditLog(sessions_dir=tmp_path, session_id="test-session")
    log.log(UserPrompt(session_id="test-session", text="hello"))
    log.log(AgentMessage(session_id="test-session", text="hi back"))

    path = tmp_path / "test-session.jsonl"
    assert path.exists()
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(lines) == 2
    assert lines[0]["event_type"] == "user_prompt"
    assert lines[1]["event_type"] == "agent_message"


def test_audit_log_read_session(tmp_path: Path) -> None:
    """read_session() returns all events in order."""
    log = AuditLog(sessions_dir=tmp_path, session_id="sess-42")
    events = [
        UserPrompt(session_id="sess-42", text="q1"),
        ToolCall(session_id="sess-42", tool_name="list_tables", args={}),
        AgentMessage(session_id="sess-42", text="found tables"),
    ]
    for e in events:
        log.log(e)

    log2 = AuditLog(sessions_dir=tmp_path, session_id="sess-42")
    loaded = log2.read_session("sess-42")
    assert len(loaded) == 3
    assert isinstance(loaded[0], UserPrompt)
    assert isinstance(loaded[1], ToolCall)
    assert isinstance(loaded[2], AgentMessage)


def test_audit_log_5_action_session(tmp_path: Path) -> None:
    """A typical 5-action session produces 5 coherent JSONL events."""
    sid = "five-action"
    log = AuditLog(sessions_dir=tmp_path, session_id=sid)
    log.log(ProfileSwitched(session_id=sid, old_profile="", new_profile="prod"))
    log.log(UserPrompt(session_id=sid, text="how many orders?"))
    log.log(
        ToolCall(session_id=sid, tool_name="run_sql", args={"sql": "SELECT COUNT(*) FROM orders"})
    )
    log.log(
        SqlExecuted(
            session_id=sid,
            sql="SELECT COUNT(*) FROM orders",
            success=True,
            row_count=1,
            results_ref=None,
        )
    )
    log.log(AgentMessage(session_id=sid, text="There are 1000 orders."))

    loaded = log.read_session(sid)
    assert len(loaded) == 5
    types = [type(e).__name__ for e in loaded]
    assert types == ["ProfileSwitched", "UserPrompt", "ToolCall", "SqlExecuted", "AgentMessage"]


def test_sql_executed_no_full_results() -> None:
    """SqlExecuted only stores a results_ref (path), not the full result set."""
    e = SqlExecuted(
        session_id="s1",
        sql="SELECT * FROM orders",
        success=True,
        row_count=1000,
        results_ref=None,  # path to parquet, or None — never the raw rows
    )
    data = e.model_dump(mode="json")
    # Ensure there's no 'rows' or 'data' key leaking full results
    assert "rows" not in data
    assert "data" not in data
