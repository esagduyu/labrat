"""Tests for AgentLoop tool-dispatch logic (M14).

LLM interaction is mocked; tests verify the loop's behavior without API calls.
Real API tests are gated by LABRAT_RUN_LLM_TESTS=1.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from labrat.agent.loop import AgentLoop, ContentBlock, TextBlock, ToolUseBlock
from labrat.agent.providers.base import ModelProvider
from labrat.agent.tools.base import Tool, ToolContext, ToolRegistry
from labrat.agent.tools.run_sql import RunSqlTool
from labrat.db.duckdb_engine import DuckDBConnection

FIXTURE_DB = Path(__file__).parent.parent / "fixtures" / "sample_dbs" / "test.duckdb"


# ── mock provider ─────────────────────────────────────────────────────────────


class _MockProvider(ModelProvider):
    """Fake provider that emits a pre-configured sequence of responses."""

    def __init__(self, responses: list[list[ContentBlock]]) -> None:
        self._responses = list(responses)
        self._call_count = 0

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[ContentBlock]:
        if self._call_count >= len(self._responses):
            raise StopAsyncIteration
        blocks = self._responses[self._call_count]
        self._call_count += 1

        async def _iter() -> AsyncIterator[ContentBlock]:
            for block in blocks:
                yield block

        return _iter()


# ── test tool ─────────────────────────────────────────────────────────────────


class _EchoInput(BaseModel):
    message: str


class _EchoTool(Tool[_EchoInput]):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo the message."

    @property
    def input_model(self) -> type[_EchoInput]:
        return _EchoInput

    async def execute(self, ctx: ToolContext, args: _EchoInput) -> object:
        return f"echoed: {args.message}"


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(_EchoTool())
    return r


@pytest.fixture()
def ctx() -> ToolContext:
    return ToolContext(connection=object(), catalog=object())


# ── basic text response ───────────────────────────────────────────────────────


async def test_loop_streams_text_blocks(registry: ToolRegistry, ctx: ToolContext) -> None:
    """A text-only response is streamed to the on_text callback."""
    provider = _MockProvider([[TextBlock(text="hello world")]])
    received: list[str] = []
    loop = AgentLoop(provider=provider, registry=registry, ctx=ctx)
    await loop.run("say hello", on_text=lambda t: received.append(t))
    assert "".join(received) == "hello world"


# ── tool dispatch round-trip ──────────────────────────────────────────────────


async def test_loop_dispatches_tool_and_continues(registry: ToolRegistry, ctx: ToolContext) -> None:
    """When the model emits a tool_use block, the loop dispatches it and sends the result back."""
    tool_use = ToolUseBlock(id="call_1", name="echo", input={"message": "ping"})
    final_text = TextBlock(text="done")
    # First turn: tool call. Second turn: final text.
    provider = _MockProvider([[tool_use], [final_text]])

    received: list[str] = []
    loop = AgentLoop(provider=provider, registry=registry, ctx=ctx)
    await loop.run("use echo", on_text=lambda t: received.append(t))

    assert "done" in "".join(received)
    assert provider._call_count == 2  # two model calls


async def test_loop_tool_result_in_history(registry: ToolRegistry, ctx: ToolContext) -> None:
    """Tool result is appended to conversation history before the next model call."""
    tool_use = ToolUseBlock(id="call_1", name="echo", input={"message": "test"})
    final_text = TextBlock(text="finished")
    provider = _MockProvider([[tool_use], [final_text]])

    loop = AgentLoop(provider=provider, registry=registry, ctx=ctx)
    await loop.run("run echo", on_text=lambda _: None)

    # Provider called twice: initial turn + after dispatching tool result
    assert provider._call_count == 2

    # Tool result block must appear in history before the second model call
    tool_result_present = any(
        m["role"] == "user"
        and isinstance(m.get("content"), list)
        and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"])
        for m in loop.history
    )
    assert tool_result_present


# ── dialect prompt loading ────────────────────────────────────────────────────


async def test_loop_uses_dialect_system_prompt(registry: ToolRegistry, ctx: ToolContext) -> None:
    """AgentLoop builds its system prompt from the given dialect when no explicit system= given."""
    captured_system: list[str] = []

    class _InspectProvider(_MockProvider):
        async def stream(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]],
            system: str,
        ) -> AsyncIterator[ContentBlock]:
            captured_system.append(system)
            return await super().stream(messages, tools, system)

    provider = _InspectProvider([[TextBlock(text="ok")]])
    loop = AgentLoop(provider=provider, registry=registry, ctx=ctx, dialect="postgres")
    await loop.run("hi", on_text=lambda _: None)

    assert len(captured_system) == 1
    assert "PostgreSQL" in captured_system[0]


async def test_loop_explicit_system_overrides_dialect(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    """An explicit system= string takes precedence over dialect-generated prompt."""
    provider = _MockProvider([[TextBlock(text="ok")]])
    loop = AgentLoop(
        provider=provider,
        registry=registry,
        ctx=ctx,
        system="custom system",
        dialect="postgres",
    )
    assert loop._system == "custom system"


# ── unknown tool ──────────────────────────────────────────────────────────────


async def test_loop_handles_unknown_tool_gracefully(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    """If the model calls a nonexistent tool, the loop returns an error result and continues."""
    bad_tool = ToolUseBlock(id="call_x", name="no_such_tool", input={})
    final_text = TextBlock(text="recovered")
    provider = _MockProvider([[bad_tool], [final_text]])

    received: list[str] = []
    loop = AgentLoop(provider=provider, registry=registry, ctx=ctx)
    await loop.run("use bad tool", on_text=lambda t: received.append(t))
    assert "recovered" in "".join(received)


# ── on_tool_call hook ─────────────────────────────────────────────────────────


def _make_loop_with_one_tool_call(registry: ToolRegistry, ctx: ToolContext) -> AgentLoop:
    """Return a loop whose provider emits exactly one tool_use then a final text turn."""
    tool_use = ToolUseBlock(id="call_hook", name="echo", input={"message": "hi"})
    final_text = TextBlock(text="done")
    provider = _MockProvider([[tool_use], [final_text]])
    return AgentLoop(provider=provider, registry=registry, ctx=ctx)


_EXPECTED_TOOL_NAME = "echo"


async def test_on_tool_call_fires_once_per_dispatch(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    calls: list[tuple[str, bool]] = []

    def on_tool_call(
        name: str, tool_input: dict[str, object], ok: bool, output: str, latency_ms: float
    ) -> None:
        calls.append((name, ok))

    loop = _make_loop_with_one_tool_call(registry, ctx)
    await loop.run("question", on_tool_call=on_tool_call)
    assert len(calls) == 1
    assert calls[0][0] == _EXPECTED_TOOL_NAME
    assert isinstance(calls[0][1], bool)


async def test_on_tool_call_optional(registry: ToolRegistry, ctx: ToolContext) -> None:
    loop = _make_loop_with_one_tool_call(registry, ctx)
    await loop.run("question")  # on_tool_call omitted → no error, behaves as before


# ── hook→trace output invariant ───────────────────────────────────────────────


async def test_run_agent_task_trace_collector_persists_output(
    tmp_path: Path, registry: ToolRegistry, ctx: ToolContext
) -> None:
    """run_agent_task forwards on_tool_call to the loop; the trace file captures
    the real tool output (the load-bearing DAB submission invariant)."""
    import json

    from labrat.agent.runner import run_agent_task
    from labrat.agent.tool_trace import append_tool_trace

    def _trace(
        tool: str,
        tool_input: dict[str, object],
        ok: bool,
        output: str,
        latency_ms: float,
    ) -> None:
        append_tool_trace(
            tmp_path,
            "agent_tool_calls.jsonl",
            tool=tool,
            input=tool_input,
            ok=ok,
            output=output,
            latency_ms=latency_ms,
        )

    tool_use = ToolUseBlock(id="call_trace", name="echo", input={"message": "hi"})
    final_text = TextBlock(text="done")
    provider = _MockProvider([[tool_use], [final_text]])

    await run_agent_task(
        prompt="hi",
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="s",
        on_tool_call=_trace,
    )

    lines = (tmp_path / "agent_tool_calls.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["tool"] == "echo"
    assert rec["output"] == "echoed: hi"  # real tool output, not a stub
    assert set(rec.keys()) == {"tool", "input", "ok", "output", "latency_ms"}


# ── real API test (gated) ─────────────────────────────────────────────────────


@pytest.mark.skipif(
    not __import__("os").environ.get("LABRAT_RUN_LLM_TESTS"),
    reason="Set LABRAT_RUN_LLM_TESTS=1 to run real API tests",
)
async def test_loop_real_api_count_query() -> None:
    """End-to-end: agent answers 'how many orders?' using the fixture DB."""
    from labrat.agent.providers.anthropic_direct import AnthropicProvider
    from labrat.agent.tools.describe_table import DescribeTableTool
    from labrat.agent.tools.list_tables import ListTablesTool

    conn = DuckDBConnection(FIXTURE_DB, read_only=True)
    conn.connect()
    catalog = conn.introspect_catalog()
    real_ctx = ToolContext(connection=conn, catalog=catalog, profile_name="test")

    real_registry = ToolRegistry()
    real_registry.register(ListTablesTool())
    real_registry.register(RunSqlTool())
    real_registry.register(DescribeTableTool())

    provider = AnthropicProvider()
    loop = AgentLoop(provider=provider, registry=real_registry, ctx=real_ctx)
    received: list[str] = []
    await loop.run(
        "How many rows are in the orders table?",
        on_text=lambda t: received.append(t),
    )
    full_response = "".join(received)
    assert any(c.isdigit() for c in full_response), "Expected a number in the response"
    conn.disconnect()
