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
from typing import Any

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
    llm_classify_provider: ModelProvider | None = None,
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
      - ``ctx.llm_fn`` injected from the loop's own provider when the caller left
        it None (enables llm_extract and, by fallback, llm_classify);
      - ``ctx.llm_classify_fn`` injected only from ``llm_classify_provider`` when
        supplied, isolating classification without redirecting llm_extract;
      - ContextLedger attached when ``enable_ledger`` (durable at ``ledger_dir``
        or a per-call temp dir);
      - optional LLMVerifier (the sufficiency judge — NOT consensus);
      - ``ctx.subagent_runner`` installed when the caller left it None (same
        caller-wins precedent as ``llm_fn``). The closure derives its
        sub-registry from THIS session's ``registry`` minus ``dispatch_subagent``
        (depth-1 guard #1) and its sub-ctx shares the connections/catalog/llm_fn
        substrate but resets ``subagent_runner`` to None (depth-1 guard #2). It
        runs a fresh, bounded ``AgentLoop`` on the same provider and the parent's
        ledger instance, splicing artifact-ref previews (via the ledger's
        ResultStore) into the sub-agent's seed prompt.

    The caller owns the loop lifecycle: run once (run_agent_task) or keep it
    across turns (TUI chat — ``loop.history`` accumulates).
    """
    if ctx.llm_fn is None:
        ctx.llm_fn = provider_llm_fn(provider, system=_LLM_FN_SYSTEM)
    if ctx.llm_classify_fn is None and llm_classify_provider is not None:
        ctx.llm_classify_fn = provider_llm_fn(
            llm_classify_provider, system=_LLM_FN_SYSTEM
        )

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

    loop = AgentLoop(
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

    if ctx.subagent_runner is None:
        parent_registry = registry
        parent_ledger = ledger  # may be None (enable_ledger=False)
        parent_loop = loop  # the loop constructed above, for trace forwarding (R1)

        def _forward_tool_call(
            name: str, args: dict[str, Any], ok: bool, output: str, latency_ms: float
        ) -> None:
            # Read active_on_tool_call AT CALL TIME (never captured) — reflects
            # whichever parent-loop run() invocation is currently in flight, and
            # is None (no forwarding) outside any run() call.
            hook = parent_loop.active_on_tool_call
            if hook is not None:
                hook(f"subagent:{name}", args, ok, output, latency_ms)

        async def _run_subagent(
            *,
            seed_prompt: str,
            artifact_refs: list[str],
            max_turns: int,
            max_tool_calls: int,
        ) -> tuple[str, int, int]:
            seed = seed_prompt
            if artifact_refs and parent_ledger is not None:
                previews: list[str] = []
                for ref in artifact_refs:
                    try:
                        previews.append(f"### {ref}\n{parent_ledger.store.preview(ref)}")
                    except Exception:
                        previews.append(f"[unresolvable ref: {ref}]")
                seed = seed + "\n\n## Provided artifacts\n\n" + "\n\n".join(previews)
            elif artifact_refs:
                seed = (
                    seed
                    + "\n\n## Provided artifacts\n\n"
                    + "\n\n".join(f"[unresolvable ref: {ref}]" for ref in artifact_refs)
                )
            sub_loop = AgentLoop(
                provider=provider,
                registry=_sub_registry(parent_registry),
                ctx=_sub_ctx(ctx),
                system=system_prompt,
                dialect=dialect,
                max_turns=max_turns,
                max_tool_calls=max_tool_calls,
                ledger=parent_ledger,
            )
            chunks: list[str] = []
            await sub_loop.run(seed, on_text=chunks.append, on_tool_call=_forward_tool_call)
            return ("".join(chunks), sub_loop.turns_used, sub_loop.tool_calls_used)

        ctx.subagent_runner = _run_subagent

    return loop


def _sub_registry(hosting: ToolRegistry) -> ToolRegistry:
    """The hosting registry minus dispatch_subagent — depth-1 guard #1.

    Derived from the HOST (never rebuilt from the standard set) so restricted
    hosts cannot be confused-deputied into wider tool access (retires the M4
    review's I1 advisory for this consumer).
    """
    sub = ToolRegistry()
    for tool in hosting.tools:
        if tool.name != "dispatch_subagent":
            sub.register(tool)
    return sub


def _sub_ctx(parent: ToolContext) -> ToolContext:
    """Shared execution substrate, fresh guard: subagent_runner stays None."""
    return ToolContext(
        connections=parent.connections,
        catalogs=parent.catalogs,
        primary=parent.primary,
        profile_name=parent.profile_name,
        read_only=parent.read_only,
        llm_fn=parent.llm_fn,
        llm_classify_fn=parent.llm_classify_fn,
        llm_classify_concurrency=parent.llm_classify_concurrency,
        subagent_runner=None,
    )
