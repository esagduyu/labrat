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
