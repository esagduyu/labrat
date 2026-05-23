"""Tests for DraftSqlTool (M16)."""

from __future__ import annotations

import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.draft_sql import DraftSqlTool


@pytest.fixture()
def ctx() -> ToolContext:
    return ToolContext(connection=object(), catalog=object())


async def test_draft_sql_tool_calls_callback(ctx: ToolContext) -> None:
    """DraftSqlTool calls the on_draft callback with the SQL string."""
    received: list[str] = []
    tool = DraftSqlTool(on_draft=received.append)
    await tool.execute(ctx, tool.input_model(sql="SELECT 1"))
    assert received == ["SELECT 1"]


async def test_draft_sql_tool_no_callback_is_safe(ctx: ToolContext) -> None:
    """DraftSqlTool works even when no on_draft callback is provided."""
    tool = DraftSqlTool()
    result = await tool.execute(ctx, tool.input_model(sql="SELECT 42"))
    assert result is not None


async def test_draft_sql_tool_has_required_properties() -> None:
    """DraftSqlTool exposes name, description, and input_model."""
    tool = DraftSqlTool()
    assert tool.name == "draft_sql"
    assert len(tool.description) > 0
    assert tool.input_model is not None


async def test_draft_sql_tool_schema_has_sql_field() -> None:
    """The tool's Anthropic schema exposes a 'sql' input field."""
    tool = DraftSqlTool()
    schema = tool.anthropic_schema()
    props = schema["input_schema"]["properties"]
    assert "sql" in props
