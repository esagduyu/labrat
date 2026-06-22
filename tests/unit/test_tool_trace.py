"""Shared tool-call trace writer (FEATURE: Codex⇄Claude parity)."""

from __future__ import annotations

import json
from pathlib import Path

from labrat.agent.tool_trace import append_tool_trace
from labrat.mcp.server import _TOOL_LOG_FILENAME, _log_tool_call


def test_append_writes_exact_schema(tmp_path: Path) -> None:
    append_tool_trace(
        tmp_path,
        "agent_tool_calls.jsonl",
        tool="run_sql",
        input={"sql": "SELECT 1"},
        ok=True,
        output="ok",
        latency_ms=12.5,
    )
    line = (tmp_path / "agent_tool_calls.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads(line) == {
        "tool": "run_sql",
        "input": {"sql": "SELECT 1"},
        "ok": True,
        "output": "ok",
        "latency_ms": 12.5,
    }


def test_append_is_one_line_per_call(tmp_path: Path) -> None:
    for i in range(3):
        append_tool_trace(
            tmp_path, "t.jsonl", tool=f"t{i}", input={}, ok=True, output="", latency_ms=0.0
        )
    assert len((tmp_path / "t.jsonl").read_text(encoding="utf-8").strip().splitlines()) == 3


def test_append_noop_on_falsy_log_dir() -> None:
    append_tool_trace(
        None, "t.jsonl", tool="t", input={}, ok=True, output="", latency_ms=0.0
    )  # no raise


def test_mcp_logger_still_writes_same_schema(tmp_path: Path) -> None:
    _log_tool_call(
        str(tmp_path),
        name="link_schema",
        arguments={"q": "x"},
        ok=True,
        output="{}",
        latency_ms=3.0,
    )
    rec = json.loads((tmp_path / _TOOL_LOG_FILENAME).read_text(encoding="utf-8").strip())
    assert rec == {
        "tool": "link_schema",
        "input": {"q": "x"},
        "ok": True,
        "output": "{}",
        "latency_ms": 3.0,
    }
