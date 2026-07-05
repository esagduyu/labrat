"""ToolContext.llm_fn: optional per-row LLM callable, default None."""

from __future__ import annotations

from labrat.agent.tools.base import ToolContext


def test_llm_fn_defaults_none_single_db() -> None:
    ctx = ToolContext(connection=object(), catalog=object())
    assert ctx.llm_fn is None


def test_llm_fn_defaults_none_multi_db() -> None:
    ctx = ToolContext(connections={"main": object()}, catalogs={"main": object()}, primary="main")
    assert ctx.llm_fn is None


async def test_llm_fn_stored_and_callable() -> None:
    async def fake_llm(prompt: str) -> str:
        return f"echo:{prompt}"

    ctx = ToolContext(connection=object(), catalog=object(), llm_fn=fake_llm)
    assert ctx.llm_fn is fake_llm
    assert await ctx.llm_fn("hi") == "echo:hi"
