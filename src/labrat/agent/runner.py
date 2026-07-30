"""run_agent_task — in-process AgentLoop runner with a structured result.

Used by:
  - ``scripts/run_task.py`` (CLI shim for arbitrary queries)
  - DAB harness ``labrat-agent`` driver
  - (Eventually) other benchmarks and the TUI chat path
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from labrat.agent.providers.base import ModelProvider
from labrat.agent.session import build_agent_session
from labrat.agent.tools.base import ToolContext, ToolRegistry


class AgentTaskResult(BaseModel):
    """One-shot agent run summary."""

    model_config = ConfigDict(frozen=True)

    final_text: str
    tool_calls: int
    latency_seconds: float
    turns: int = 0
    turn_budget_exhausted: bool = False


async def run_agent_task(
    *,
    prompt: str,
    ctx: ToolContext,
    registry: ToolRegistry,
    provider: ModelProvider,
    llm_classify_provider: ModelProvider | None = None,
    system_prompt: str,
    max_turns: int | None = None,
    max_tool_calls: int | None = None,
    verify: bool = False,
    max_verify_rounds: int = 2,
    on_tool_call: Callable[[str, dict[str, Any], bool, str, float], None] | None = None,
    enable_ledger: bool = True,
    ledger_dir: Path | None = None,
    ledger_max_bytes: int | None = None,
) -> AgentTaskResult:
    """Run a single agent task and return the assistant's final text + tool count.

    The caller owns the ToolContext, ToolRegistry, provider, and system prompt —
    this function does not assume any particular tool set or model.

    ``max_turns`` and ``max_tool_calls`` are forwarded to the underlying
    ``AgentLoop``; ``None`` means unbounded. When ``verify`` is set, an
    ``LLMVerifier`` backed by the same provider gates the final answer for up to
    ``max_verify_rounds`` rounds (off by default — it costs an extra LLM call per
    would-be-final answer).

    ``enable_ledger`` (default True) attaches a ContextLedger so oversized tool
    outputs enter model history as bounded summaries + artifact_refs; the
    ``on_tool_call`` hook still receives full payloads. The ResultStore root is
    ``ledger_dir`` when given (pass the run dir for durable provenance);
    otherwise a per-call temp directory (``tempfile.mkdtemp``, OS-reaped).
    ``enable_ledger=False`` restores bare-loop behavior.

    This runner also injects ``ctx.llm_fn`` (via ``provider_llm_fn``) when the caller
    left it None, enabling the per-row llm_extract/llm_classify tools on this path.
    ``llm_classify_provider`` optionally isolates classification calls onto a
    dedicated model without redirecting ``llm_extract``; when omitted both keep
    using ``provider`` exactly as before.

    Rate-limit fail-fast is a ctx concern, not a runner kwarg: callers that
    want provider 429s to escape tool dispatch (benchmark runners) set
    ``ctx.raise_rate_limits = True`` on the ctx they own before calling.
    """
    text_parts: list[str] = []

    def on_text(text: str) -> None:
        text_parts.append(text)

    loop = build_agent_session(
        ctx=ctx,
        registry=registry,
        provider=provider,
        llm_classify_provider=llm_classify_provider,
        system_prompt=system_prompt,
        verify=verify,
        max_verify_rounds=max_verify_rounds,
        enable_ledger=enable_ledger,
        ledger_dir=ledger_dir,
        ledger_max_bytes=ledger_max_bytes,
        max_turns=max_turns,
        max_tool_calls=max_tool_calls,
    )
    t0 = time.monotonic()
    await loop.run(prompt, on_text=on_text, on_tool_call=on_tool_call)
    latency = time.monotonic() - t0

    return AgentTaskResult(
        final_text="".join(text_parts),
        tool_calls=loop.tool_calls_used,
        latency_seconds=latency,
        turns=loop.turns_used,
        turn_budget_exhausted=loop.turn_budget_exhausted,
    )
