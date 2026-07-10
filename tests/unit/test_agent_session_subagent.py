"""build_agent_session installs the subagent runner (scoped, guarded, ledger-shared)."""

from labrat.agent.providers.anthropic_direct import AnthropicProvider
from labrat.agent.session import PINNED_DEFAULT_MODEL, build_agent_session
from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.agent.tools.dispatch_subagent import DispatchSubagentTool


def _session(ctx: ToolContext, registry: ToolRegistry):
    return build_agent_session(
        ctx=ctx,
        registry=registry,
        provider=AnthropicProvider(model=PINNED_DEFAULT_MODEL),
        system_prompt="s",
        enable_ledger=True,
    )


def test_runner_installed_and_caller_wins() -> None:
    ctx = ToolContext()
    _session(ctx, ToolRegistry())
    assert ctx.subagent_runner is not None

    async def mine(**_: object) -> tuple[str, int, int]:
        return ("", 0, 0)

    ctx2 = ToolContext(subagent_runner=mine)
    _session(ctx2, ToolRegistry())
    assert ctx2.subagent_runner is mine  # caller injection wins (llm_fn precedent)


def test_sub_registry_derived_from_hosting_registry() -> None:
    from labrat.agent.session import _sub_registry
    from labrat.agent.tools.run_sql import RunSqlTool

    hosting = ToolRegistry()
    hosting.register(RunSqlTool())
    hosting.register(DispatchSubagentTool())
    sub = _sub_registry(hosting)
    names = {t.name for t in sub.tools}
    assert names == {"run_sql"}  # subset of the HOST, minus the dispatch tool


def test_sub_ctx_shares_substrate_and_is_guarded() -> None:
    from labrat.agent.session import _sub_ctx

    conn, cat = object(), object()
    parent = ToolContext(
        connections={"main": conn},
        catalogs={"main": cat},
        primary="main",
        profile_name="p1",
        read_only=True,
    )

    async def fake_llm(prompt: str) -> str:
        return "x"

    parent.llm_fn = fake_llm
    sub = _sub_ctx(parent)
    assert sub.connections["main"] is conn and sub.catalogs["main"] is cat
    assert sub.primary == "main" and sub.profile_name == "p1"
    assert sub.read_only is True and sub.llm_fn is fake_llm
    assert sub.subagent_runner is None  # depth-1 guard #2
