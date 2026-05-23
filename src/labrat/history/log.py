"""Append-only JSONL query history log."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

_LOG_DIR = Path.home() / ".local" / "share" / "labrat" / "history"


def log_query(
    *,
    profile: str,
    sql_final: str,
    executed: bool,
    success: bool,
    execution_time_ms: float = 0.0,
    row_count: int | None = None,
    error_message: str | None = None,
) -> None:
    """Append one query event to the profile's JSONL history file."""
    path = _LOG_DIR / f"{profile}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "profile": profile,
        "sql_final": sql_final,
        "executed": executed,
        "success": success,
        "execution_time_ms": execution_time_ms,
        "row_count": row_count,
        "error_message": error_message,
    }
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")
