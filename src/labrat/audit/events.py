"""Pydantic event models for audit log (M19).

IMPORTANT: Sensitive data (credentials, full result sets, secrets) MUST NOT
appear in any event. Use references (paths, IDs) instead of inline data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field


class BaseEvent(BaseModel):
    session_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class UserPrompt(BaseEvent):
    event_type: Literal["user_prompt"] = "user_prompt"
    text: str


class AgentMessage(BaseEvent):
    event_type: Literal["agent_message"] = "agent_message"
    text: str


class ToolCall(BaseEvent):
    event_type: Literal["tool_call"] = "tool_call"
    tool_name: str
    args: dict[str, Any]


class ToolResult(BaseEvent):
    event_type: Literal["tool_result"] = "tool_result"
    tool_name: str
    ok: bool
    result_ref: str  # summary or reference — NOT the full payload


class SqlExecuted(BaseEvent):
    """Records that a SQL statement was executed.

    Full result sets are NEVER logged; only a reference path (if written to
    disk) or None.  Logging secrets or credentials here is forbidden.
    """

    event_type: Literal["sql_executed"] = "sql_executed"
    sql: str
    success: bool
    row_count: int | None
    results_ref: str | None  # path to Parquet file, or None


class ChartCreated(BaseEvent):
    event_type: Literal["chart_created"] = "chart_created"
    chart_spec: dict[str, Any]
    results_ref: str  # path to the data file used for the chart


class FindingPinned(BaseEvent):
    event_type: Literal["finding_pinned"] = "finding_pinned"
    finding_id: str
    question: str
    sql: str  # the query that produced this finding


class ExportRequested(BaseEvent):
    event_type: Literal["export_requested"] = "export_requested"
    finding_ids: list[str]
    output_path: str


class ProfileSwitched(BaseEvent):
    event_type: Literal["profile_switched"] = "profile_switched"
    old_profile: str
    new_profile: str


AuditEvent = Annotated[
    UserPrompt
    | AgentMessage
    | ToolCall
    | ToolResult
    | SqlExecuted
    | ChartCreated
    | FindingPinned
    | ExportRequested
    | ProfileSwitched,
    Field(discriminator="event_type"),
]

_EVENT_TYPES: dict[str, type[BaseEvent]] = {
    "user_prompt": UserPrompt,
    "agent_message": AgentMessage,
    "tool_call": ToolCall,
    "tool_result": ToolResult,
    "sql_executed": SqlExecuted,
    "chart_created": ChartCreated,
    "finding_pinned": FindingPinned,
    "export_requested": ExportRequested,
    "profile_switched": ProfileSwitched,
}


def parse_event(data: dict[str, Any]) -> BaseEvent:
    """Parse a raw dict into the appropriate event model."""
    event_type = data.get("event_type", "")
    cls = _EVENT_TYPES.get(event_type)
    if cls is None:
        raise ValueError(f"Unknown event_type: {event_type!r}")
    return cls.model_validate(data)
