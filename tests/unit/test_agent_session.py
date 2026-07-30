"""build_agent_session / resolve_provider — the shared TUI+runner factory."""

from pathlib import Path

from labrat.agent.providers.anthropic_direct import AnthropicProvider
from labrat.agent.providers.claude_code import ClaudeCodeProvider
from labrat.agent.session import PINNED_DEFAULT_MODEL, build_agent_session, resolve_provider
from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.profile.model import Profile


def test_resolve_auto_prefers_anthropic_when_key_set(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    provider, warning = resolve_provider(Profile(name="p", dialect="duckdb"))
    assert isinstance(provider, AnthropicProvider)
    assert warning is None


def test_resolve_auto_falls_back_to_claude_code_with_warning(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider, warning = resolve_provider(Profile(name="p", dialect="duckdb"))
    assert isinstance(provider, ClaudeCodeProvider)
    assert warning is not None and "degraded" in warning.lower()


def test_resolve_explicit_provider_and_model(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    profile = Profile(
        name="p", dialect="duckdb", agent_provider="anthropic", agent_model="claude-opus-4-8"
    )
    provider, warning = resolve_provider(profile)
    assert isinstance(provider, AnthropicProvider)
    assert warning is None


def test_pinned_default_model() -> None:
    assert PINNED_DEFAULT_MODEL == "claude-sonnet-4-6"


def test_build_agent_session_injects_llm_fn_and_ledger(tmp_path: Path) -> None:
    ctx = ToolContext(connections={"main": object()}, catalogs={"main": object()}, primary="main")
    assert ctx.llm_fn is None
    loop = build_agent_session(
        ctx=ctx,
        registry=ToolRegistry(),
        provider=AnthropicProvider(model=PINNED_DEFAULT_MODEL),
        system_prompt="test system",
        ledger_dir=tmp_path / "ledger",
    )
    assert ctx.llm_fn is not None  # per-row primitives enabled
    assert loop._ledger is not None  # ledger attached  # type: ignore[reportPrivateUsage]
    assert loop._verifier is None  # verify defaults off  # type: ignore[reportPrivateUsage]


def test_build_agent_session_verify_and_no_ledger(tmp_path: Path) -> None:
    ctx = ToolContext(connections={"main": object()}, catalogs={"main": object()}, primary="main")
    loop = build_agent_session(
        ctx=ctx,
        registry=ToolRegistry(),
        provider=AnthropicProvider(model=PINNED_DEFAULT_MODEL),
        system_prompt="s",
        verify=True,
        enable_ledger=False,
    )
    assert loop._verifier is not None  # type: ignore[reportPrivateUsage]
    assert loop._ledger is None  # type: ignore[reportPrivateUsage]


def test_build_agent_session_respects_caller_llm_fn(tmp_path: Path) -> None:
    async def my_llm(prompt: str) -> str:
        return "x"

    ctx = ToolContext(
        connections={"main": object()}, catalogs={"main": object()}, primary="main", llm_fn=my_llm
    )
    build_agent_session(
        ctx=ctx,
        registry=ToolRegistry(),
        provider=AnthropicProvider(model=PINNED_DEFAULT_MODEL),
        system_prompt="s",
        enable_ledger=False,
    )
    assert ctx.llm_fn is my_llm  # caller injection wins


def test_ledger_budget_defaults_to_8000_and_is_overridable(tmp_path: Path) -> None:
    """The in-process ledger on the labrat-agent path was hardcoded to the 8000-byte
    LedgerBudget default with no override surface, while the claude-mcp server-side
    ledger got raised to 64000 on 2026-07-24 precisely because 8 KB truncates
    search_reference_docs / describe_table grounding (those run 8-22 KB).

    The accepted 74.18% Luna entry runs this path and made 398 search_reference_docs
    calls into an 8 KB cap, with no get_artifact tool to recover the remainder.
    See docs/dab-sonnet5-vs-luna-gap-analysis.md §(g).
    """
    from labrat.agent.providers.anthropic_direct import AnthropicProvider

    def _loop(**kw: object):
        return build_agent_session(
            ctx=ToolContext(
                connections={"main": object()}, catalogs={"main": object()}, primary="main"
            ),
            registry=ToolRegistry(),
            provider=AnthropicProvider(model="claude-sonnet-4-6"),
            ledger_dir=tmp_path,
            **kw,  # type: ignore[arg-type]
        )

    assert _loop()._ledger is not None
    assert _loop()._ledger._budget.max_bytes == 8000  # unchanged default
    assert _loop(ledger_max_bytes=64000)._ledger._budget.max_bytes == 64000
    assert _loop(ledger_max_bytes=64000)._ledger._budget.max_rows == 50  # rows untouched
