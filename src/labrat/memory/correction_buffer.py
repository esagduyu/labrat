"""Session-scoped correction capture for the TUI harvest flow (M5 T2b surface).

Pure bookkeeping — NO LLM calls, no I/O. The buffer accumulates cheap
candidates while the user works; SessionHarvester's extractors (which do call
an LLM) only run when the user explicitly triggers harvest-review.
"""

from __future__ import annotations

import difflib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from labrat.history.events import QueryEvent


@dataclass(frozen=True)
class ChatCorrection:
    """A user chat message that followed agent-produced SQL."""

    user_message: str
    context_sql: str


@dataclass
class CorrectionBuffer:
    _chats: list[ChatCorrection] = field(default_factory=list[ChatCorrection])
    _edits: list[QueryEvent] = field(default_factory=list[QueryEvent])

    def add_chat(self, user_message: str, context_sql: str) -> None:
        self._chats.append(ChatCorrection(user_message, context_sql))

    def add_edit(self, *, profile: str, thread_id: str, draft_sql: str, executed_sql: str) -> bool:
        """Record an agent-draft → user-edit pair. Returns False when identical."""
        if draft_sql.strip() == executed_sql.strip():
            return False
        diff = "\n".join(
            difflib.unified_diff(
                draft_sql.splitlines(),
                executed_sql.splitlines(),
                fromfile="draft",
                tofile="executed",
                lineterm="",
            )
        )
        self._edits.append(
            QueryEvent(
                timestamp=datetime.now(tz=UTC),
                profile=profile,
                thread_id=thread_id,
                version_id=str(uuid.uuid4()),
                sql_final=executed_sql,
                sql_initial=draft_sql,
                edit_diff=diff,
                executed=True,
            )
        )
        return True

    @property
    def pending_count(self) -> int:
        return len(self._chats) + len(self._edits)

    def drain(self) -> tuple[list[ChatCorrection], list[QueryEvent]]:
        chats, edits = self._chats, self._edits
        self._chats, self._edits = [], []
        return chats, edits
