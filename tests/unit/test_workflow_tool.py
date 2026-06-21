"""The workflow tracking tool (FEATURE_ROADMAP #30)."""

from __future__ import annotations

import pytest

from labrat.agent.data_tools import build_data_tools_registry
from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.workflow import WorkflowTool


async def test_no_arg_returns_full_checklist() -> None:
    tool = WorkflowTool()
    out = await tool.execute(ToolContext(profile_name="p"), tool.input_model())
    assert "clarify" in out.checklist
    assert "review" in out.checklist
    assert set(out.statuses) >= {"clarify", "query", "verify_joins"}


async def test_marking_a_step_advances_it() -> None:
    tool = WorkflowTool()
    ctx = ToolContext(profile_name="p")
    await tool.execute(ctx, tool.input_model(step="clarify", status="done"))
    out = await tool.execute(ctx, tool.input_model(step="query", status="doing"))
    assert out.statuses["clarify"] == "done"
    assert out.statuses["query"] == "doing"


async def test_unknown_step_raises() -> None:
    tool = WorkflowTool()
    with pytest.raises(ValueError):
        await tool.execute(ToolContext(profile_name="p"), tool.input_model(step="bogus"))


async def test_state_is_isolated_per_profile() -> None:
    tool = WorkflowTool()
    await tool.execute(
        ToolContext(profile_name="a"), tool.input_model(step="clarify", status="done")
    )
    out_b = await tool.execute(ToolContext(profile_name="b"), tool.input_model())
    assert out_b.statuses["clarify"] == "pending"  # profile b is independent


async def test_repair_doing_increments_attempts() -> None:
    tool = WorkflowTool()
    ctx = ToolContext(profile_name="p")
    await tool.execute(ctx, tool.input_model(step="repair", status="doing"))
    out = await tool.execute(ctx, tool.input_model(step="repair", status="doing"))
    assert out.repair_attempts == 2


async def test_registered_in_data_tools_registry() -> None:
    names = {s["name"] for s in build_data_tools_registry().to_anthropic_schemas()}
    assert "workflow" in names
