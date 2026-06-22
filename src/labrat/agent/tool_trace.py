"""Shared per-call tool-trace writer — the single source of the trace schema
used by BOTH DAB drivers (claude-mcp's MCP server and the labrat-agent loop),
so the submission package's traces are schema-identical across providers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["TOOL_TRACE_FIELDS", "append_tool_trace"]

TOOL_TRACE_FIELDS = ("tool", "input", "ok", "output", "latency_ms")


def append_tool_trace(
    log_dir: str | Path | None,
    filename: str,
    *,
    tool: str,
    input: dict[str, Any],
    ok: bool,
    output: str,
    latency_ms: float,
) -> None:
    """Append one JSON line ``{tool, input, ok, output, latency_ms}`` to
    ``<log_dir>/<filename>``. No-op when ``log_dir`` is falsy."""
    if not log_dir:
        return
    record = {"tool": tool, "input": input, "ok": ok, "output": output, "latency_ms": latency_ms}
    dest = Path(log_dir)
    dest.mkdir(parents=True, exist_ok=True)
    with (dest / filename).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
