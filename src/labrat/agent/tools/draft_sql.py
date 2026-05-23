"""DraftSqlTool: emit draft SQL into the editor without executing it (M16)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from labrat.agent.tools.base import Tool, ToolContext


class _Input(BaseModel):
    sql: str = Field(description="The SQL query to draft into the editor pane.")


class DraftSqlTool(Tool[_Input]):
    """Writes draft SQL to the editor pane without executing it.

    Use this tool to show your work as you construct a query.  The SQL will
    appear in the editor so the user can follow along and intervene before
    execution.
    """

    def __init__(self, on_draft: Callable[[str], None] | None = None) -> None:
        self._on_draft = on_draft

    @property
    def name(self) -> str:
        return "draft_sql"

    @property
    def description(self) -> str:
        return (
            "Write draft SQL to the editor pane without executing it. "
            "Use this to show your work as you construct a query."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> object:
        if self._on_draft is not None:
            self._on_draft(args.sql)
        return {"status": "drafted", "sql": args.sql}

    def anthropic_schema(self) -> dict[str, Any]:
        schema = super().anthropic_schema()
        return schema
