"""run_agent_task: drives AgentLoop with a fake provider and asserts the shape."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel

from labrat.agent.loop import ContentBlock, TextBlock, ToolUseBlock
from labrat.agent.providers.base import ModelProvider, RateLimitError
from labrat.agent.runner import run_agent_task
from labrat.agent.tools.base import Tool, ToolContext, ToolRegistry


class _FakeProvider(ModelProvider):
    """Replay a scripted sequence of content-block lists across successive stream() calls."""

    def __init__(self, script: list[list[ContentBlock]]) -> None:
        self._script = script
        self._call = 0

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[ContentBlock]:
        blocks = self._script[self._call]
        self._call += 1

        async def _emit() -> AsyncIterator[ContentBlock]:
            for b in blocks:
                yield b

        return _emit()


class _NoopInput(BaseModel):
    pass


class _NoopTool(Tool[_NoopInput]):
    @property
    def name(self) -> str:
        return "noop"

    @property
    def description(self) -> str:
        return "Return a fixed string."

    @property
    def input_model(self) -> type[_NoopInput]:
        return _NoopInput

    async def execute(self, ctx: ToolContext, args: _NoopInput) -> dict[str, str]:
        return {"value": "ok"}


async def test_runner_returns_final_text_and_counts_tool_calls() -> None:
    ctx = ToolContext(connections={"primary": object()}, catalogs={"primary": object()})
    registry = ToolRegistry()
    registry.register(_NoopTool())

    # Turn 1: one tool call. Turn 2: a closing text. Runner should count 1 tool call
    # and surface only the final text it streamed (which is the turn-2 text).
    provider = _FakeProvider(
        [
            [ToolUseBlock(id="t1", name="noop", input={})],
            [TextBlock(text="The answer is 42.")],
        ]
    )

    result = await run_agent_task(
        prompt="how many?",
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="you are a test",
    )
    assert result.final_text == "The answer is 42."
    assert result.tool_calls == 1
    assert result.latency_seconds >= 0


async def test_runner_handles_no_tool_calls() -> None:
    ctx = ToolContext(connections={"primary": object()}, catalogs={"primary": object()})
    registry = ToolRegistry()
    provider = _FakeProvider([[TextBlock(text="Direct answer.")]])

    result = await run_agent_task(
        prompt="what?",
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="be brief",
    )
    assert result.final_text == "Direct answer."
    assert result.tool_calls == 0
    assert result.turn_budget_exhausted is False


async def test_runner_respects_max_turns_cap() -> None:
    """max_turns prevents further provider calls past the cap."""
    ctx = ToolContext(connections={"primary": object()}, catalogs={"primary": object()})
    registry = ToolRegistry()
    registry.register(_NoopTool())

    # Provider would keep asking for tools forever. Cap at 2 turns.
    provider = _FakeProvider(
        [
            [ToolUseBlock(id="t1", name="noop", input={})],
            [ToolUseBlock(id="t2", name="noop", input={})],
            [ToolUseBlock(id="t3", name="noop", input={})],
            [TextBlock(text="unreached")],
        ]
    )

    result = await run_agent_task(
        prompt="loop",
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="capped",
        max_turns=2,
    )
    assert result.tool_calls == 2  # exactly 2 tool dispatches over 2 turns
    assert result.final_text == ""  # never reached the closing text
    assert result.turns == 2
    assert result.turn_budget_exhausted is True


async def test_runner_respects_max_tool_calls_cap() -> None:
    """max_tool_calls dispatches up to the budget and drops any overflow within a round."""
    ctx = ToolContext(connections={"primary": object()}, catalogs={"primary": object()})
    registry = ToolRegistry()
    registry.register(_NoopTool())

    # Model emits 3 tools in one round; cap is 2.
    provider = _FakeProvider(
        [
            [
                ToolUseBlock(id="t1", name="noop", input={}),
                ToolUseBlock(id="t2", name="noop", input={}),
                ToolUseBlock(id="t3", name="noop", input={}),
            ],
            [TextBlock(text="unreached")],
        ]
    )

    result = await run_agent_task(
        prompt="batch",
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="capped",
        max_tool_calls=2,
    )
    assert result.tool_calls == 2


async def test_runner_tool_rate_limit_degrades_by_default(limited_tool: Tool[Any]) -> None:
    """Product callers: a 429 inside a tool never escapes run_agent_task."""
    ctx = ToolContext(connections={"primary": object()}, catalogs={"primary": object()})
    registry = ToolRegistry()
    registry.register(limited_tool)
    provider = _FakeProvider(
        [
            [ToolUseBlock(id="t1", name="limited", input={})],
            [TextBlock(text="degraded gracefully")],
        ]
    )

    result = await run_agent_task(
        prompt="try it",
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="test",
    )
    assert result.final_text == "degraded gracefully"
    assert ctx.raise_rate_limits is False


async def test_runner_ctx_rate_limit_flag_enables_fail_fast(limited_tool: Tool[Any]) -> None:
    """A ctx constructed with raise_rate_limits=True (the DAB labrat-agent driver
    sets it on the ctx it owns): the 429 escapes the run. There is no runner kwarg."""
    ctx = ToolContext(
        connections={"primary": object()},
        catalogs={"primary": object()},
        raise_rate_limits=True,
    )
    registry = ToolRegistry()
    registry.register(limited_tool)
    provider = _FakeProvider(
        [
            [ToolUseBlock(id="t1", name="limited", input={})],
            [TextBlock(text="unreached")],
        ]
    )

    with pytest.raises(RateLimitError):
        await run_agent_task(
            prompt="try it",
            ctx=ctx,
            registry=registry,
            provider=provider,
            system_prompt="test",
        )
    assert ctx.raise_rate_limits is True


async def test_runner_counts_multiple_tool_calls_across_turns() -> None:
    ctx = ToolContext(connections={"primary": object()}, catalogs={"primary": object()})
    registry = ToolRegistry()
    registry.register(_NoopTool())

    provider = _FakeProvider(
        [
            [ToolUseBlock(id="t1", name="noop", input={})],
            [ToolUseBlock(id="t2", name="noop", input={})],
            [
                TextBlock(text="first part. "),
                ToolUseBlock(id="t3", name="noop", input={}),
            ],
            [TextBlock(text="done.")],
        ]
    )

    result = await run_agent_task(
        prompt="multi-step",
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="multi",
    )
    assert result.tool_calls == 3
    assert "done." in result.final_text
