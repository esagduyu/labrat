"""dispatch_subagent: delegate a scoped sub-task to a fresh, bounded agent loop.

T1d Phase 2 (spec docs/superpowers/specs/2026-07-09-dispatch-subagent-design.md).
The tool composes the seed (sub-task + context hint + Scent) and delegates
execution to ctx.subagent_runner — the build_agent_session-injected closure
that owns provider/registry/ledger. Hosts without an in-process loop (MCP
server, claude-mcp) have no runner: the tool returns a structured self-error,
exactly the llm_extract capability precedent. Depth-1 is structural: the
runner's sub-registry excludes this tool AND the sub-ctx runner is None.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from labrat.agent.tools.base import Tool, ToolContext, reraise_if_rate_limited
from labrat.agent.tools.serialization import LedgerPayloadKind

_MAX_TURNS_CEILING = 8
_MAX_TOOL_CALLS_CEILING = 15


class _Input(BaseModel):
    sub_task: str = Field(description="The self-contained task for the sub-agent.")
    artifact_refs: list[str] = Field(
        default_factory=list,
        description="result:// refs whose previews the sub-agent should receive.",
    )
    context_hint: str | None = Field(
        default=None, description="Optional extra grounding for the sub-agent."
    )
    max_turns: int = Field(default=6, description="Sub-agent turn budget (1-8).")
    max_tool_calls: int = Field(default=10, description="Sub-agent tool-call budget (1-15).")

    @field_validator("max_turns")
    @classmethod
    def _clamp_turns(cls, v: int) -> int:
        return max(1, min(v, _MAX_TURNS_CEILING))

    @field_validator("max_tool_calls")
    @classmethod
    def _clamp_calls(cls, v: int) -> int:
        return max(1, min(v, _MAX_TOOL_CALLS_CEILING))


class _Output(BaseModel):
    ok: bool
    final_text: str
    turns_used: int = 0
    tool_calls_used: int = 0
    error: str | None = None

    def ledger_payload(self) -> tuple[LedgerPayloadKind, object] | None:
        return ("json", self.model_dump())


class DispatchSubagentTool(Tool[_Input]):
    """Delegate a bounded sub-task to a scoped sub-agent; returns by ledger ref."""

    @property
    def name(self) -> str:
        return "dispatch_subagent"

    @property
    def description(self) -> str:
        return (
            "Delegate a self-contained sub-task (an exploration, a side-query, a "
            "verification) to a scoped sub-agent with its own small budget. The "
            "sub-agent sees ONLY the sub_task, optional context_hint, previews of "
            "any artifact_refs you pass, and relevant reference notes — never this "
            "conversation. Use it to keep your own context lean. Unavailable on "
            "hosts without an in-process agent loop."
        )

    @property
    def input_model(self) -> type[_Input]:
        return _Input

    async def execute(self, ctx: ToolContext, args: _Input) -> _Output:
        runner = ctx.subagent_runner
        if runner is None:
            return _Output(
                ok=False,
                final_text="",
                error=(
                    "dispatch_subagent unavailable: no subagent runner on this host "
                    "(requires an in-process AgentLoop provider)"
                ),
            )
        seed = await _compose_seed(ctx, args)
        try:
            final_text, turns, calls = await runner(
                seed_prompt=seed,
                artifact_refs=list(args.artifact_refs),
                max_turns=args.max_turns,
                max_tool_calls=args.max_tool_calls,
            )
        except Exception as exc:  # sub-loop failure must not kill the parent turn...
            # ...except a provider 429 under benchmark fail-fast, which must
            # propagate (not degrade) so the harness records an infra row.
            reraise_if_rate_limited(ctx, exc)
            return _Output(ok=False, final_text="", error=str(exc))
        return _Output(ok=True, final_text=final_text, turns_used=turns, tool_calls_used=calls)


async def _compose_seed(ctx: ToolContext, args: _Input) -> str:
    parts: list[str] = ["## Sub-task", args.sub_task.strip()]
    if args.context_hint:
        parts.append(args.context_hint.strip())
    scent = await _scent_notes(ctx, args.sub_task)
    if scent:
        parts.extend(["## Relevant reference notes", scent])
    return "\n\n".join(parts)


async def _scent_notes(ctx: ToolContext, question: str, top_k: int = 3) -> str:
    """Top-k Scent sections for the sub-task via the real retrieval tool (deterministic)."""
    from labrat.agent.tools.search_reference_docs import SearchReferenceDocsTool

    try:
        tool = SearchReferenceDocsTool()
        out = await tool.execute(
            ctx, tool.input_model.model_validate({"question": question, "top_k": top_k})
        )
    except Exception:
        return ""  # retrieval must never block dispatch
    lines: list[str] = []
    for doc in out.results:
        for sec in doc.sections:
            lines.append(f"### {doc.domain} — {sec.heading}\n{sec.body}")
    return "\n\n".join(lines)
