"""Query history event model (M28)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class QueryEvent(BaseModel):
    """A single query execution event.

    Never stores result data — only SQL text and metadata.
    PII in SQL literals is redacted by QueryHistoryLog before writing.
    """

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    profile: str
    thread_id: str
    version_id: str
    sql_final: str
    sql_initial: str | None = None
    edit_diff: str | None = None
    executed: bool = False
    success: bool | None = None
    execution_time_ms: int | None = None
    row_count: int | None = None
    error_message: str | None = None
