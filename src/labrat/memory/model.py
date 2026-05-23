"""Memory model for self-healing corrections (M31)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class MemoryScope(StrEnum):
    global_ = "global"
    table_ = "table"
    thread_ = "thread"


class MemoryKind(StrEnum):
    edit_derived = "edit_derived"
    chat_correction = "chat_correction"
    explicit_user_rule = "explicit_user_rule"


class Memory(BaseModel):
    """A single durable correction the agent applies automatically."""

    model_config = ConfigDict(frozen=False)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    profile: str
    scope: MemoryScope
    kind: MemoryKind
    text: str
    table_scope: str | None = None
    source: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    last_applied_at: datetime | None = None
    application_count: int = 0
    embedding: list[float] | None = None
