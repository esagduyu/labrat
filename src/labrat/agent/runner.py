"""run_agent_task — in-process AgentLoop runner with a structured result.

Used by:
  - ``scripts/run_task.py`` (CLI shim for arbitrary queries)
  - DAB harness ``labrat-agent`` driver
  - (Eventually) other benchmarks and the TUI chat path
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from labrat.agent.loop import AgentLoop
from labrat.agent.providers.base import ModelProvider
from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.agent.verifier import provider_llm_fn
from labrat.results.store import ResultStore
from labrat.runtime.context_ledger import ContextLedger

# System prompt for the injected per-row llm_fn (llm_extract / llm_classify).
# Kept terse and format-obsessed: each per-row prompt carries its own full
# instructions; this only reinforces the output discipline.
_LLM_FN_SYSTEM = (
    "You are a precise per-row data-extraction engine. Follow the output-format "
    "instructions in each request exactly: reply with ONLY the requested JSON object "
    "or category value — no prose, no markdown fences, no explanation."
)


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
    verify: bool = False,
    max_verify_rounds: int = 2,
    on_tool_call: Callable[[str, dict[str, Any], bool, str, float], None] | None = None,
    enable_ledger: bool = True,
    ledger_dir: Path | None = None,
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
    """
    text_parts: list[str] = []

    def on_text(text: str) -> None:
        text_parts.append(text)

    verifier = None
    if verify:
        from labrat.agent.verifier import LLMVerifier

        verifier = LLMVerifier(provider_llm_fn(provider))

    # Per-row LLM primitives (llm_extract / llm_classify) need an injected llm_fn.
    # The loop's own provider doubles as the per-row caller (same model + billing).
    # Only set when the caller hasn't provided one — caller injection wins; every
    # other ToolContext builder (TUI, MCP server, DAB claude-mcp) leaves it None.
    if ctx.llm_fn is None:
        ctx.llm_fn = provider_llm_fn(provider, system=_LLM_FN_SYSTEM)

    ledger: ContextLedger | None = None
    if enable_ledger:
        root = (
            ledger_dir
            if ledger_dir is not None
            else Path(tempfile.mkdtemp(prefix="labrat-ledger-"))
        )
        ledger = ContextLedger(ResultStore(root))

    loop = AgentLoop(
        provider=provider,
        registry=registry,
        ctx=ctx,
        system=system_prompt,
        max_turns=max_turns,
        max_tool_calls=max_tool_calls,
        verifier=verifier,
        max_verify_rounds=max_verify_rounds,
        ledger=ledger,
    )
    t0 = time.monotonic()
    await loop.run(prompt, on_text=on_text, on_tool_call=on_tool_call)
    latency = time.monotonic() - t0

    return AgentTaskResult(
        final_text="".join(text_parts),
        tool_calls=loop.tool_calls_used,
        latency_seconds=latency,
    )
