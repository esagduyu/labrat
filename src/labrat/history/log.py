"""JSONL-based query history log (M28).

Appends one QueryEvent per line to ~/.local/share/labrat/history/{profile}.jsonl.
PII in SQL literals is redacted before writing.
"""

from __future__ import annotations

from pathlib import Path

from labrat.history.events import QueryEvent
from labrat.history.pii import redact_pii

_DEFAULT_HISTORY_DIR = Path.home() / ".local" / "share" / "labrat" / "history"


class QueryHistoryLog:
    """Append-only JSONL query log, one file per profile."""

    def __init__(self, history_dir: Path | None = None) -> None:
        self._dir = history_dir or _DEFAULT_HISTORY_DIR

    def _path(self, profile: str) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        return self._dir / f"{profile}.jsonl"

    def append(self, event: QueryEvent) -> None:
        redacted_sql = redact_pii(event.sql_final)
        if redacted_sql != event.sql_final:
            event = event.model_copy(update={"sql_final": redacted_sql})
        path = self._path(event.profile)
        with path.open("a") as f:
            f.write(event.model_dump_json() + "\n")

    def read_profile(self, profile: str) -> list[QueryEvent]:
        path = self._path(profile)
        if not path.exists():
            return []
        events: list[QueryEvent] = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                events.append(QueryEvent.model_validate_json(line))
        return events
