"""Thread, Version, and Finding data models (M17)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class Thread(BaseModel):
    """A conversation thread. Each thread contains an ordered sequence of Versions."""

    id: str
    name: str
    profile_name: str
    created_at: datetime
    parent_version_id: str | None = None


class Version(BaseModel):
    """A snapshot of a thread's SQL artifact and conversation history."""

    id: str
    thread_id: str
    sql: str
    results_ref: str | None
    chat_history: list[dict[str, Any]]
    created_at: datetime


class Finding(BaseModel):
    """A user-pinned (question, sql, results) tuple — the export unit."""

    id: str
    version_id: str
    question: str
    sql: str
    results_ref: str | None
    chart_spec: dict[str, Any] | None
    note: str
    pinned_at: datetime
