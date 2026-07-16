"""build_agent_session installs the subagent runner (scoped, guarded, ledger-shared)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import duckdb
import pytest

from labrat.agent.loop import ContentBlock, TextBlock, ToolUseBlock
from labrat.agent.providers.anthropic_direct import AnthropicProvider
from labrat.agent.providers.base import ModelProvider, RateLimitError
from labrat.agent.session import PINNED_DEFAULT_MODEL, build_agent_session
from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.agent.tools.dispatch_subagent import DispatchSubagentTool
from labrat.agent.tools.llm_classify import LlmClassifyTool
from labrat.db.duckdb_engine import DuckDBConnection


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


def test_sub_ctx_propagates_raise_rate_limits() -> None:
    from labrat.agent.session import _sub_ctx

    parent = ToolContext(
        connections={"main": object()},
        catalogs={"main": object()},
        primary="main",
        raise_rate_limits=True,
    )
    sub = _sub_ctx(parent)
    assert sub.raise_rate_limits is True
    # And the product default stays off.
    assert _sub_ctx(ToolContext(connections={}, catalogs={})).raise_rate_limits is False


def test_sub_ctx_propagates_active_maps() -> None:
    from labrat.agent.session import _sub_ctx

    parent = ToolContext(connections={}, catalogs={}, active_maps=["sales"])
    assert _sub_ctx(parent).active_maps == ["sales"]
    # Unset stays unset (retrieval byte-identical to today).
    assert _sub_ctx(ToolContext(connections={}, catalogs={})).active_maps is None


def test_sub_ctx_inherits_remaining_classify_budget() -> None:
    from labrat.agent.session import _sub_ctx

    parent = ToolContext(connections={}, catalogs={}, llm_classify_row_budget=200)
    parent.llm_classify_rows_used = 150
    sub = _sub_ctx(parent)
    assert sub.llm_classify_row_budget == 50  # only what the parent has left
    assert sub.llm_classify_rows_used == 0

    # Exhausted parent -> zero remaining (llm_classify self-errors immediately).
    exhausted = ToolContext(connections={}, catalogs={}, llm_classify_row_budget=4)
    exhausted.llm_classify_rows_used = 4
    assert _sub_ctx(exhausted).llm_classify_row_budget == 0

    # No parent budget -> no sub budget.
    assert _sub_ctx(ToolContext(connections={}, catalogs={})).llm_classify_row_budget is None


# ── cumulative classify budget across delegation ─────────────────────────────


def _articles_conn(tmp_path: Path, rows: int) -> DuckDBConnection:
    path = str(tmp_path / "articles.duckdb")
    raw = duckdb.connect(path)
    raw.execute("CREATE TABLE articles (id INTEGER, headline VARCHAR)")
    raw.executemany(
        "INSERT INTO articles VALUES (?, ?)",
        [(i, f"Business headline {i}") for i in range(rows)],
    )
    raw.close()
    conn = DuckDBConnection(path=path, read_only=False)
    conn.connect()
    return conn


async def _always_business(_prompt: str) -> str:
    return "Business"


async def test_sub_ctx_enforces_parent_remaining_classify_budget(tmp_path: Path) -> None:
    """Parent 150/200 used -> the sub-agent can classify at most 50 rows; the 51st self-errors."""
    from labrat.agent.session import _sub_ctx

    conn = _articles_conn(tmp_path, rows=60)
    parent = ToolContext(
        connections={"main": conn},
        catalogs={"main": None},
        primary="main",
        llm_fn=_always_business,
        llm_classify_row_budget=200,
    )
    parent.llm_classify_rows_used = 150
    sub = _sub_ctx(parent)
    tool = LlmClassifyTool()
    args = tool.input_model(
        table="articles", text_column="headline", labels=["Business"], key_columns=["id"]
    )

    first = await tool.execute(sub, args)
    assert first.ok and first.rows_processed == 50  # clamped to the parent's remainder
    assert first.rows_remaining == 0

    second = await tool.execute(sub, args)  # the 51st row
    assert not second.ok
    assert second.error is not None and "budget exhausted" in second.error
    conn.disconnect()


class _ScriptProvider(ModelProvider):
    """Replay a scripted sequence of content-block lists across stream() calls."""

    def __init__(self, script: list[list[ContentBlock]]) -> None:
        self._script = script
        self._call = 0

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[ContentBlock]:
        blocks = self._script[self._call]
        self._call += 1

        async def _emit() -> AsyncIterator[ContentBlock]:
            for b in blocks:
                yield b

        return _emit()


async def test_subagent_usage_flows_back_into_parent_counter(tmp_path: Path) -> None:
    """Rows the sub-agent classifies draw down the PARENT's cumulative budget."""
    conn = _articles_conn(tmp_path, rows=3)
    parent = ToolContext(
        connections={"main": conn},
        catalogs={"main": None},
        primary="main",
        llm_fn=_always_business,
        llm_classify_row_budget=10,
    )
    parent.llm_classify_rows_used = 4
    registry = ToolRegistry()
    registry.register(LlmClassifyTool())
    provider = _ScriptProvider(
        [
            [
                ToolUseBlock(
                    id="t1",
                    name="llm_classify",
                    input={
                        "table": "articles",
                        "text_column": "headline",
                        "labels": ["Business"],
                        "key_columns": ["id"],
                    },
                )
            ],
            [TextBlock(text="classified")],
        ]
    )
    build_agent_session(
        ctx=parent,
        registry=registry,
        provider=provider,
        system_prompt="s",
        enable_ledger=False,
    )
    assert parent.subagent_runner is not None
    final_text, _turns, calls = await parent.subagent_runner(
        seed_prompt="classify the articles",
        artifact_refs=[],
        max_turns=3,
        max_tool_calls=3,
    )
    assert "classified" in final_text and calls == 1
    assert parent.llm_classify_rows_used == 4 + 3  # sub-run usage added back
    conn.disconnect()


# ── dispatch_subagent rate-limit seam ─────────────────────────────────────────


async def test_dispatch_subagent_reraises_rate_limit_when_opted_in() -> None:
    """A 429 escaping the sub-loop must propagate when ctx.raise_rate_limits is set."""

    async def limited_runner(
        *,
        seed_prompt: str,
        artifact_refs: list[str],
        max_turns: int,
        max_tool_calls: int,
    ) -> tuple[str, int, int]:
        raise RateLimitError()

    ctx = ToolContext(
        connections={},
        catalogs={},
        subagent_runner=limited_runner,
        raise_rate_limits=True,
    )
    registry = ToolRegistry()
    registry.register(DispatchSubagentTool())
    with pytest.raises(RateLimitError):  # escapes execute() AND registry dispatch
        await registry.dispatch("dispatch_subagent", {"sub_task": "probe"}, ctx)


async def test_dispatch_subagent_rate_limit_degrades_by_default() -> None:
    """Product contexts keep the structured-error contract for sub-loop failures."""

    async def limited_runner(
        *,
        seed_prompt: str,
        artifact_refs: list[str],
        max_turns: int,
        max_tool_calls: int,
    ) -> tuple[str, int, int]:
        raise RateLimitError()

    ctx = ToolContext(connections={}, catalogs={}, subagent_runner=limited_runner)
    tool = DispatchSubagentTool()
    out = await tool.execute(ctx, tool.input_model(sub_task="probe"))
    assert not out.ok
    assert out.error is not None and "rate limit" in out.error.lower()
