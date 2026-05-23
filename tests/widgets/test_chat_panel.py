"""Tests for ChatPanel widget (M16)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from textual.app import App, ComposeResult

from labrat.agent.loop import AgentLoop, ContentBlock, TextBlock, ToolUseBlock
from labrat.agent.providers.base import ModelProvider
from labrat.agent.tools.base import ToolContext, ToolRegistry
from labrat.widgets.chat_panel import ChatPanel

# ── mock provider ─────────────────────────────────────────────────────────────


class _MockProvider(ModelProvider):
    def __init__(self, responses: list[list[ContentBlock]]) -> None:
        self._responses = list(responses)
        self._idx = 0

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[ContentBlock]:
        # Return the next configured response; clamp to last one if exhausted.
        idx = min(self._idx, len(self._responses) - 1)
        blocks = self._responses[idx]
        self._idx += 1

        async def _emit() -> AsyncIterator[ContentBlock]:
            for b in blocks:
                yield b

        return _emit()


# ── helper app ────────────────────────────────────────────────────────────────


class _ChatApp(App[None]):
    def __init__(self, agent_loop: AgentLoop) -> None:
        super().__init__()
        self._agent_loop = agent_loop

    def compose(self) -> ComposeResult:
        panel = ChatPanel(id="chat")
        panel.set_agent_loop(self._agent_loop)
        yield panel


def _make_loop(responses: list[list[ContentBlock]]) -> AgentLoop:
    provider = _MockProvider(responses)
    registry = ToolRegistry()
    ctx = ToolContext(connection=object(), catalog=object())
    return AgentLoop(provider=provider, registry=registry, ctx=ctx)


# ── tests ─────────────────────────────────────────────────────────────────────


async def test_chat_panel_renders() -> None:
    """ChatPanel mounts without error and shows the input field."""
    loop = _make_loop([[TextBlock(text="hello")]])
    async with _ChatApp(loop).run_test() as pilot:
        await pilot.pause()
        panel = pilot.app.query_one("#chat", ChatPanel)
        assert panel is not None
        from textual.widgets import Input

        assert pilot.app.query_one("Input", Input) is not None


async def test_chat_panel_submit_message() -> None:
    """Running _start_agent directly streams the agent response into the transcript."""
    loop = _make_loop([[TextBlock(text="42 rows")]])
    async with _ChatApp(loop).run_test() as pilot:
        await pilot.pause()
        panel = pilot.app.query_one("#chat", ChatPanel)
        panel._start_agent("how many orders?")
        # wait for worker to finish
        await pilot.pause(delay=0.3)
        assert "42 rows" in panel.transcript


async def test_chat_panel_busy_flag_set_during_run() -> None:
    """is_agent_busy is True while the agent is running."""
    import asyncio

    barrier: asyncio.Event = asyncio.Event()
    blocked: asyncio.Event = asyncio.Event()

    class _BlockingProvider(ModelProvider):
        async def stream(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]],
            system: str,
        ) -> AsyncIterator[ContentBlock]:
            blocked.set()
            await barrier.wait()

            async def _emit() -> AsyncIterator[ContentBlock]:
                yield TextBlock(text="done")

            return _emit()

    provider = _BlockingProvider()
    registry = ToolRegistry()
    ctx = ToolContext(connection=object(), catalog=object())
    loop = AgentLoop(provider=provider, registry=registry, ctx=ctx)

    async with _ChatApp(loop).run_test() as pilot:
        await pilot.pause()
        panel = pilot.app.query_one("#chat", ChatPanel)
        panel._start_agent("test")
        # yield to let the worker start and block on barrier
        await asyncio.wait_for(blocked.wait(), timeout=2.0)
        assert panel.is_agent_busy is True
        barrier.set()
        await pilot.pause(delay=0.3)
        assert panel.is_agent_busy is False


async def test_chat_panel_tool_call_appears_in_transcript() -> None:
    """Tool calls show up as action cards in the chat transcript."""
    # Second response is the final text after tool dispatch
    loop = _make_loop(
        [
            [ToolUseBlock(id="tu1", name="list_tables", input={})],
            [TextBlock(text="Found it")],
        ]
    )
    async with _ChatApp(loop).run_test() as pilot:
        await pilot.pause()
        panel = pilot.app.query_one("#chat", ChatPanel)
        panel._start_agent("show tables")
        await pilot.pause(delay=0.3)
        assert "list_tables" in panel.transcript
