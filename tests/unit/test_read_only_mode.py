"""Read-only Analyst mode (Unit A): ToolContext.read_only + Tool.is_mutating + dispatch gate."""

from __future__ import annotations

from pydantic import BaseModel

from labrat.agent.tools.base import Tool, ToolContext, ToolRegistry


class _NoopInput(BaseModel):
    value: str = "x"


class _ReaderTool(Tool[_NoopInput]):
    @property
    def name(self) -> str:
        return "reader"

    @property
    def description(self) -> str:
        return "A read-only tool."

    @property
    def input_model(self) -> type[_NoopInput]:
        return _NoopInput

    async def execute(self, ctx: ToolContext, args: _NoopInput) -> object:
        return "read-ok"


class _WriterTool(Tool[_NoopInput]):
    mutating = True

    @property
    def name(self) -> str:
        return "writer"

    @property
    def description(self) -> str:
        return "A structurally mutating tool."

    @property
    def input_model(self) -> type[_NoopInput]:
        return _NoopInput

    async def execute(self, ctx: ToolContext, args: _NoopInput) -> object:
        return "wrote"


def test_tool_context_read_only_defaults_false() -> None:
    assert ToolContext().read_only is False


def test_tool_context_read_only_flag_set() -> None:
    assert ToolContext(read_only=True).read_only is True


def test_default_is_mutating_false() -> None:
    assert _ReaderTool().is_mutating(_NoopInput()) is False


def test_class_attr_mutating_true() -> None:
    assert _WriterTool().is_mutating(_NoopInput()) is True


async def test_dispatch_blocks_mutating_tool_when_read_only() -> None:
    reg = ToolRegistry()
    reg.register(_WriterTool())
    res = await reg.dispatch("writer", {}, ToolContext(read_only=True))
    assert res.ok is False
    assert res.value is None
    assert res.error == "blocked: read-only Analyst mode"


async def test_dispatch_allows_reader_tool_when_read_only() -> None:
    reg = ToolRegistry()
    reg.register(_ReaderTool())
    res = await reg.dispatch("reader", {}, ToolContext(read_only=True))
    assert res.ok is True
    assert res.value == "read-ok"


async def test_dispatch_allows_mutating_tool_when_not_read_only() -> None:
    # Regression: read_only defaults False → zero behavior change for all callers.
    reg = ToolRegistry()
    reg.register(_WriterTool())
    res = await reg.dispatch("writer", {}, ToolContext())
    assert res.ok is True
    assert res.value == "wrote"
