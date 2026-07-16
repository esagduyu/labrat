"""run_agent_task injects ctx.llm_fn from its provider (per-row LLM primitives)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from labrat.agent.loop import ContentBlock, TextBlock
from labrat.agent.providers.base import ModelProvider
from labrat.agent.runner import run_agent_task
from labrat.agent.tools.base import ToolContext, ToolRegistry


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


async def test_runner_injects_llm_fn() -> None:
    ctx = ToolContext(connections={"primary": object()}, catalogs={"primary": object()})
    provider = _FakeProvider(
        [
            [TextBlock(text="Direct answer.")],  # consumed by the agent run itself
            [TextBlock(text="per-row reply")],  # consumed by the injected llm_fn below
        ]
    )
    await run_agent_task(
        prompt="q", ctx=ctx, registry=ToolRegistry(), provider=provider, system_prompt="s"
    )
    assert ctx.llm_fn is not None
    assert await ctx.llm_fn("extract this") == "per-row reply"


async def test_runner_preserves_caller_injected_llm_fn() -> None:
    async def mine(prompt: str) -> str:
        return "mine"

    ctx = ToolContext(
        connections={"primary": object()}, catalogs={"primary": object()}, llm_fn=mine
    )
    provider = _FakeProvider([[TextBlock(text="Direct answer.")]])
    await run_agent_task(
        prompt="q", ctx=ctx, registry=ToolRegistry(), provider=provider, system_prompt="s"
    )
    assert ctx.llm_fn is mine


async def test_runner_can_isolate_llm_classify_on_dedicated_provider() -> None:
    ctx = ToolContext(connections={"primary": object()}, catalogs={"primary": object()})
    main = _FakeProvider([[TextBlock(text="Direct answer.")]])
    classifier = _FakeProvider([[TextBlock(text="Business")]])
    await run_agent_task(
        prompt="q",
        ctx=ctx,
        registry=ToolRegistry(),
        provider=main,
        llm_classify_provider=classifier,
        system_prompt="s",
    )
    assert ctx.llm_fn is not None
    assert ctx.llm_classify_fn is not None
    assert await ctx.llm_classify_fn("classify this") == "Business"
