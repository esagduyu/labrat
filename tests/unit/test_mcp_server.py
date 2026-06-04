import json
from pathlib import Path

from labrat.mcp.server import _log_tool_call


def test_log_tool_call_writes_jsonl_line(tmp_path: Path) -> None:
    _log_tool_call(
        str(tmp_path),
        name="run_sql",
        arguments={"sql": "SELECT 1"},
        ok=True,
        output="[[1]]",
        latency_ms=12.5,
    )
    log_file = tmp_path / "mcp_tool_calls.jsonl"
    assert log_file.exists()
    lines = log_file.read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["tool"] == "run_sql"
    assert rec["input"] == {"sql": "SELECT 1"}
    assert rec["ok"] is True
    assert rec["output"] == "[[1]]"
    assert rec["latency_ms"] == 12.5


def test_log_tool_call_appends(tmp_path: Path) -> None:
    for i in range(3):
        _log_tool_call(
            str(tmp_path), name="list_tables", arguments={}, ok=True, output=str(i), latency_ms=1.0
        )
    lines = (tmp_path / "mcp_tool_calls.jsonl").read_text().splitlines()
    assert len(lines) == 3


def test_log_tool_call_noop_without_dir(tmp_path: Path) -> None:
    # No directory configured → silent no-op, no file, no exception.
    _log_tool_call(None, name="run_sql", arguments={}, ok=True, output="x", latency_ms=1.0)
    assert list(tmp_path.iterdir()) == []
