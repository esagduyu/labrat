"""Append-only JSONL audit log writer (M19)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from labrat.audit.events import BaseEvent, parse_event

_DEFAULT_DIR = Path.home() / ".local" / "share" / "labrat" / "sessions"


class AuditLog:
    """Writes and reads audit events as JSONL.

    Each session maps to one file: ``<sessions_dir>/{session_id}.jsonl``.
    Events are appended atomically (one JSON object per line).
    """

    def __init__(
        self,
        sessions_dir: Path | None = None,
        session_id: str | None = None,
    ) -> None:
        self._dir = sessions_dir or _DEFAULT_DIR
        self._session_id = session_id or str(uuid.uuid4())
        self._path = self._dir / f"{self._session_id}.jsonl"

    @property
    def session_id(self) -> str:
        return self._session_id

    def log(self, event: BaseEvent) -> None:
        """Append an event to the session log."""
        self._dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.model_dump(mode="json")) + "\n"
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line)

    def read_session(self, session_id: str) -> list[BaseEvent]:
        """Read all events for the given session ID in order."""
        path = self._dir / f"{session_id}.jsonl"
        if not path.exists():
            return []
        events: list[BaseEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                events.append(parse_event(json.loads(line)))
        return events
