"""run_agent_task ledger toggle: default-on bounding, enable_ledger=False bare."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from labrat.agent.loop import ContentBlock, TextBlock, ToolUseBlock
from labrat.agent.providers.base import ModelProvider
from labrat.agent.runner import run_agent_task
from labrat.agent.tools.base import Tool, ToolContext, ToolRegistry

BIG_TEXT = "x" * 20_000  # over the 8000-byte default budget


class _CapturingProvider(ModelProvider):
    """Scripted provider that snapshots the messages of every stream() call."""

    def __init__(self, script: list[list[ContentBlock]]) -> None:
        self._script = script
        self._call = 0
        self.captured: list[list[dict[str, Any]]] = []

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[ContentBlock]:
        self.captured.append(list(messages))
        blocks = self._script[self._call]
        self._call += 1

        async def _emit() -> AsyncIterator[ContentBlock]:
            for b in blocks:
                yield b

        return _emit()


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


def _setup() -> tuple[ToolContext, ToolRegistry, _CapturingProvider]:
    ctx = ToolContext(connections={"primary": object()}, catalogs={"primary": object()})
    registry = ToolRegistry()
    registry.register(_BigTool())
    provider = _CapturingProvider(
        [
            [ToolUseBlock(id="t1", name="big_output", input={})],
            [TextBlock(text="done")],
        ]
    )
    return ctx, registry, provider


def _tool_result_content(messages: list[dict[str, Any]]) -> str:
    for m in messages:
        if m["role"] == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    return b["content"]
    raise AssertionError("no tool_result in captured messages")


async def test_default_ledger_bounds_model_visible_result(tmp_path: Path) -> None:
    ctx, registry, provider = _setup()
    result = await run_agent_task(
        prompt="go",
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="s",
        ledger_dir=tmp_path,
    )
    assert result.final_text == "done"
    content = _tool_result_content(provider.captured[1])
    assert len(content.encode("utf-8")) < len(BIG_TEXT)
    assert "artifact_ref: result://" in content
    # the artifact landed under the caller-provided ledger_dir
    assert list(tmp_path.glob("*/*.json"))


async def test_enable_ledger_false_restores_bare_behavior(tmp_path: Path) -> None:
    ctx, registry, provider = _setup()
    await run_agent_task(
        prompt="go",
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="s",
        enable_ledger=False,
        ledger_dir=tmp_path,
    )
    assert _tool_result_content(provider.captured[1]) == BIG_TEXT
    assert not list(tmp_path.glob("*/*"))


async def test_default_ledger_uses_temp_dir_when_no_ledger_dir() -> None:
    ctx, registry, provider = _setup()
    result = await run_agent_task(
        prompt="go",
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="s",
    )
    # default-on with no explicit dir still bounds (root = per-call temp dir)
    assert result.tool_calls == 1
    content = _tool_result_content(provider.captured[1])
    assert "artifact_ref: result://" in content


async def test_on_tool_call_still_gets_full_payload_by_default(tmp_path: Path) -> None:
    ctx, registry, provider = _setup()
    outputs: list[str] = []

    def on_tool_call(
        name: str, tool_input: dict[str, Any], ok: bool, output: str, latency_ms: float
    ) -> None:
        outputs.append(output)

    await run_agent_task(
        prompt="go",
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="s",
        on_tool_call=on_tool_call,
        ledger_dir=tmp_path,
    )
    assert outputs == [BIG_TEXT]  # DAB trace-validity invariant holds ledger-on


async def test_ledger_max_bytes_raises_the_model_visible_cap(tmp_path: Path) -> None:
    """A 20 KB tool result is truncated by the 8000-byte default but survives intact
    when the budget is raised — the labrat-agent equivalent of the 64 KB fix that
    2026-07-24 applied only to the claude-mcp server-side ledger.

    Real payloads this bites: search_reference_docs / describe_table grounding runs
    8-22 KB, and the accepted 74.18% Luna entry made 398 such calls into the 8 KB cap.
    """
    ctx, registry, provider = _setup()
    await run_agent_task(
        prompt="go",
        ctx=ctx,
        registry=registry,
        provider=provider,
        system_prompt="s",
        ledger_dir=tmp_path,
        ledger_max_bytes=64_000,
    )
    content = _tool_result_content(provider.captured[1])
    assert BIG_TEXT in content, "20 KB payload should survive a 64 KB budget intact"
