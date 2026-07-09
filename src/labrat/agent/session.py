"""Shared agent-session factory: one place that knows how to wire a real agent.

Used by ``run_agent_task`` (one-shot: benchmarks, scripts/run_task.py) and the
TUI chat path (persistent loop across turns). Building the loop here — llm_fn
injection, Context Ledger, optional verifier — keeps the two paths from
drifting apart, which is exactly what happened before this module existed
(see docs/tui-integration-handoff.md).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from labrat.agent.loop import AgentLoop
from labrat.agent.providers import build_provider
from labrat.agent.providers.anthropic_direct import AnthropicProvider
from labrat.agent.providers.base import ModelProvider
from labrat.agent.providers.claude_code import ClaudeCodeProvider
from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.agent.verifier import LLMVerifier, provider_llm_fn
from labrat.profile.model import Profile
from labrat.results.store import ResultStore
from labrat.runtime.context_ledger import ContextLedger

PINNED_DEFAULT_MODEL = "claude-sonnet-4-6"

# System prompt for the injected per-row llm_fn (llm_extract / llm_classify).
# Kept terse and format-obsessed: each per-row prompt carries its own full
# instructions; this only reinforces the output discipline.
_LLM_FN_SYSTEM = (
    "You are a precise per-row data-extraction engine. Follow the output-format "
    "instructions in each request exactly: reply with ONLY the requested JSON object "
    "or category value — no prose, no markdown fences, no explanation."
)

_DEGRADED_WARNING = (
    "No ANTHROPIC_API_KEY found — using the claude CLI (Max plan). "
    "Tool round-trips are degraded on this path; set an API key for full reliability."
)


def resolve_provider(profile: Profile) -> tuple[ModelProvider, str | None]:
    """Resolve the profile's provider setting to a concrete ModelProvider.

    Returns ``(provider, degraded_warning)``; the warning is non-None only when
    ``"auto"`` had to fall back to the claude CLI. Models are always pinned
    explicitly — a CLI default silently falling through to Opus burns Max-plan
    budget ~5x faster.
    """
    model = profile.agent_model or PINNED_DEFAULT_MODEL
    if profile.agent_provider == "auto":
        if os.environ.get("ANTHROPIC_API_KEY"):
            return AnthropicProvider(model=model), None
        return ClaudeCodeProvider(model=model), _DEGRADED_WARNING
    return build_provider(profile.agent_provider, model), None


def build_agent_session(
    *,
    ctx: ToolContext,
    registry: ToolRegistry,
    provider: ModelProvider,
    system_prompt: str = "",
    dialect: str = "duckdb",
    verify: bool = False,
    max_verify_rounds: int = 2,
    enable_ledger: bool = True,
    ledger_dir: Path | None = None,
    max_turns: int | None = None,
    max_tool_calls: int | None = None,
) -> AgentLoop:
    """Return a fully wired, persistent AgentLoop.

    Wiring performed (mirrors what run_agent_task always did):
      - ``ctx.llm_fn`` injected from the loop's own provider when the caller
        left it None (enables llm_extract/llm_classify; caller injection wins);
      - ContextLedger attached when ``enable_ledger`` (durable at ``ledger_dir``
        or a per-call temp dir);
      - optional LLMVerifier (the sufficiency judge — NOT consensus).

    The caller owns the loop lifecycle: run once (run_agent_task) or keep it
    across turns (TUI chat — ``loop.history`` accumulates).
    """
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

    verifier: LLMVerifier | None = None
    if verify:
        verifier = LLMVerifier(provider_llm_fn(provider))

    return AgentLoop(
        provider=provider,
        registry=registry,
        ctx=ctx,
        system=system_prompt,
        dialect=dialect,
        max_turns=max_turns,
        max_tool_calls=max_tool_calls,
        verifier=verifier,
        max_verify_rounds=max_verify_rounds,
        ledger=ledger,
    )
