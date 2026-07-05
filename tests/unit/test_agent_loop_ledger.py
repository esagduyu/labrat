"""AgentLoop × ContextLedger: byte-identity without a ledger, bounding with one."""  # noqa: RUF002

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from labrat.agent.loop import AgentLoop, ContentBlock, TextBlock, ToolUseBlock
from labrat.agent.providers.base import ModelProvider
from labrat.agent.tools.base import Tool, ToolContext, ToolRegistry
from labrat.results.store import ResultStore
from labrat.runtime.context_ledger import ContextLedger

BIG_TEXT = "x" * 20_000  # over the 8000-byte default budget


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
        blocks = self._responses[self._call_count]
        self._call_count += 1

        async def _iter() -> AsyncIterator[ContentBlock]:
            for block in blocks:
                yield block

        return _iter()


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


class _BigInput(BaseModel):
    pass


class _BigTool(Tool[_BigInput]):
    @property
    def name(self) -> str:
        return "big_output"

    @property
    def description(self) -> str:
        return "Return a deliberately oversized string."

    @property
    def input_model(self) -> type[_BigInput]:
        return _BigInput

    async def execute(self, ctx: ToolContext, args: _BigInput) -> object:
        return BIG_TEXT


@pytest.fixture()
def registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(_EchoTool())
    r.register(_BigTool())
    return r


@pytest.fixture()
def ctx() -> ToolContext:
    return ToolContext(connection=object(), catalog=object())


def _script(tool_name: str, tool_input: dict[str, Any]) -> list[list[ContentBlock]]:
    return [
        [ToolUseBlock(id="c1", name=tool_name, input=tool_input)],
        [TextBlock(text="done")],
    ]


def _tool_result_contents(loop: AgentLoop) -> list[str]:
    out: list[str] = []
    for m in loop.history:
        if m["role"] == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    out.append(b["content"])
    return out


async def test_no_ledger_history_carries_full_string(
    registry: ToolRegistry, ctx: ToolContext
) -> None:
    """ledger=None → tool_result content is exactly str(dispatch.value) (today)."""
    provider = _MockProvider(_script("big_output", {}))
    loop = AgentLoop(provider=provider, registry=registry, ctx=ctx)
    await loop.run("go")
    assert _tool_result_contents(loop) == [BIG_TEXT]


async def test_ledger_under_budget_history_is_byte_identical(
    registry: ToolRegistry, ctx: ToolContext, tmp_path: Path
) -> None:
    provider = _MockProvider(_script("echo", {"message": "hi"}))
    loop = AgentLoop(
        provider=provider,
        registry=registry,
        ctx=ctx,
        ledger=ContextLedger(ResultStore(tmp_path)),
    )
    await loop.run("go")
    assert _tool_result_contents(loop) == ["echoed: hi"]


async def test_ledger_bounds_oversized_result_and_roundtrips(
    registry: ToolRegistry, ctx: ToolContext, tmp_path: Path
) -> None:
    store = ResultStore(tmp_path)
    provider = _MockProvider(_script("big_output", {}))
    loop = AgentLoop(provider=provider, registry=registry, ctx=ctx, ledger=ContextLedger(store))
    await loop.run("go")
    (content,) = _tool_result_contents(loop)
    assert len(content.encode("utf-8")) < len(BIG_TEXT)
    ref = next(
        line.removeprefix("artifact_ref: ")
        for line in content.splitlines()
        if line.startswith("artifact_ref: ")
    )
    assert store.get(ref) == {"tool": "big_output", "text": BIG_TEXT}


async def test_on_tool_call_receives_full_payload_with_ledger(
    registry: ToolRegistry, ctx: ToolContext, tmp_path: Path
) -> None:
    outputs: list[str] = []

    def on_tool_call(
        name: str, tool_input: dict[str, Any], ok: bool, output: str, latency_ms: float
    ) -> None:
        outputs.append(output)

    provider = _MockProvider(_script("big_output", {}))
    loop = AgentLoop(
        provider=provider,
        registry=registry,
        ctx=ctx,
        ledger=ContextLedger(ResultStore(tmp_path)),
    )
    await loop.run("go", on_tool_call=on_tool_call)
    assert outputs == [BIG_TEXT]  # trace/audit hook gets the FULL payload
    assert _tool_result_contents(loop) != [BIG_TEXT]  # ...but history is bounded


async def test_error_dispatch_path_unchanged_with_ledger(
    registry: ToolRegistry, ctx: ToolContext, tmp_path: Path
) -> None:
    provider = _MockProvider(_script("no_such_tool", {}))
    loop = AgentLoop(
        provider=provider,
        registry=registry,
        ctx=ctx,
        ledger=ContextLedger(ResultStore(tmp_path)),
    )
    await loop.run("go")
    (content,) = _tool_result_contents(loop)
    assert content == "Error: Unknown tool: 'no_such_tool'"
