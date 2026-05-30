"""run_agent_task — in-process AgentLoop runner with a structured result.

Used by:
  - ``scripts/run_task.py`` (CLI shim for arbitrary queries)
  - DAB harness ``labrat-agent`` driver
  - (Eventually) other benchmarks and the TUI chat path
"""

from __future__ import annotations

import time

from pydantic import BaseModel, ConfigDict

from labrat.agent.loop import AgentLoop
from labrat.agent.providers.base import ModelProvider
from labrat.agent.tools.base import ToolContext, ToolRegistry


class AgentTaskResult(BaseModel):
    """One-shot agent run summary."""

    model_config = ConfigDict(frozen=True)

    final_text: str
    tool_calls: int
    latency_seconds: float


async def run_agent_task(
    *,
    prompt: str,
    ctx: ToolContext,
    registry: ToolRegistry,
    provider: ModelProvider,
    system_prompt: str,
    max_turns: int | None = None,
    max_tool_calls: int | None = None,
) -> AgentTaskResult:
    """Run a single agent task and return the assistant's final text + tool count.

    The caller owns the ToolContext, ToolRegistry, provider, and system prompt —
    this function does not assume any particular tool set or model.

    ``max_turns`` and ``max_tool_calls`` are forwarded to the underlying
    ``AgentLoop``; ``None`` means unbounded.
    """
    text_parts: list[str] = []

    def on_text(text: str) -> None:
        text_parts.append(text)

    loop = AgentLoop(
        provider=provider,
        registry=registry,
        ctx=ctx,
        system=system_prompt,
        max_turns=max_turns,
        max_tool_calls=max_tool_calls,
    )
    t0 = time.monotonic()
    await loop.run(prompt, on_text=on_text)
    latency = time.monotonic() - t0

    return AgentTaskResult(
        final_text="".join(text_parts),
        tool_calls=loop.tool_calls_used,
        latency_seconds=latency,
    )
