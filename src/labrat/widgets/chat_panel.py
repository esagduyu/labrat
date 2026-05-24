"""ChatPanel: conversational interface with streaming agent responses (M16)."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, RichLog


class ChatPanel(Widget):
    """Chat panel with user input and streaming agent conversation history.

    Layout (top to bottom):
      - RichLog  — conversation history (completed turns)
      - RichLog  — current streaming agent response (cleared when turn ends)
      - Input    — user input field

    Use ``set_agent_loop(loop)`` to wire up an AgentLoop, then user messages
    submitted via the Input will trigger ``loop.run()`` in a worker.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "stop_agent", "Stop agent", show=False),
    ]

    DEFAULT_CSS = """
    ChatPanel {
        height: 1fr;
        layout: vertical;
    }
    ChatPanel #history {
        height: 1fr;
        border: solid $surface;
        overflow-x: hidden;
    }
    ChatPanel #streaming {
        height: auto;
        max-height: 10;
        border: solid $accent;
        display: none;
        overflow-x: hidden;
    }
    ChatPanel #streaming.visible {
        display: block;
    }
    ChatPanel Input {
        dock: bottom;
    }
    """

    is_agent_busy: reactive[bool] = reactive(False)

    # ── messages ──────────────────────────────────────────────────────────────

    class AgentText(Message):
        """Posted when the agent emits a text chunk."""

        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class AgentToolCall(Message):
        """Posted when the agent calls a tool."""

        def __init__(self, name: str, args: dict[str, Any]) -> None:
            super().__init__()
            self.name = name
            self.args = args

    class AgentDone(Message):
        """Posted when the agent finishes a turn."""

    # ── init ──────────────────────────────────────────────────────────────────

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._agent_loop: Any = None  # AgentLoop; typed as Any to avoid circular import
        self._transcript_lines: list[str] = []
        self._stream_buf: list[str] = []
        # (rich_markup, is_trace) — retained for toggle reflow
        self._history_rich: list[tuple[str, bool]] = []
        self._show_traces: bool = True

    def set_agent_loop(self, loop: Any) -> None:
        """Wire an AgentLoop into this panel."""
        self._agent_loop = loop

    # ── layout ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield RichLog(id="history", highlight=False, markup=True, wrap=True, min_width=1)
        yield RichLog(id="streaming", highlight=False, markup=True, wrap=True, min_width=1)
        yield Input(id="user-input", placeholder="Ask a question…")

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def transcript(self) -> str:
        """All text that has appeared in the chat (history + current stream)."""
        lines = list(self._transcript_lines) + self._stream_buf
        return "\n".join(lines)

    # ── input handling ────────────────────────────────────────────────────────

    @on(Input.Submitted, "#user-input")
    def _on_submit(self, event: Input.Submitted) -> None:
        message = event.value.strip()
        if not message or self.is_agent_busy:
            return
        event.input.value = ""
        self._append_history(f"[bold blue]You:[/bold blue] {message}", f"You: {message}")
        self._start_agent(message)

    # ── agent worker ──────────────────────────────────────────────────────────

    @work(exclusive=True)
    async def _start_agent(self, message: str) -> None:
        if self._agent_loop is None:
            return
        self.is_agent_busy = True
        streaming = self.query_one("#streaming", RichLog)
        streaming.add_class("visible")
        streaming.clear()
        self._stream_buf.clear()
        self._append_streaming("[dim italic]✎ agent thinking…[/dim italic]", "")

        def on_text(text: str) -> None:
            self._stream_buf.append(text)
            streaming.clear()
            streaming.write("".join(self._stream_buf))
            self.post_message(ChatPanel.AgentText(text))

        _agent_error: Exception | None = None
        try:
            # Monkey-patch: wrap registry dispatch to emit AgentToolCall messages.
            orig_dispatch = self._agent_loop._registry.dispatch

            async def _traced_dispatch(name: str, args: dict[str, Any], ctx: Any) -> Any:
                self.post_message(ChatPanel.AgentToolCall(name=name, args=args))
                args_str = json.dumps(args, separators=(",", ":"))
                tool_line = f"[dim]▸[/dim] [bold]{name}[/bold]({args_str})"
                self._append_history(tool_line, f"▸ {name}({args_str})", is_trace=True)
                return await orig_dispatch(name, args, ctx)

            self._agent_loop._registry.dispatch = _traced_dispatch
            try:
                await self._agent_loop.run(message, on_text=on_text)
            except Exception as e:
                _agent_error = e
            finally:
                self._agent_loop._registry.dispatch = orig_dispatch
        finally:
            full_response = "".join(self._stream_buf)
            streaming.remove_class("visible")
            streaming.clear()
            if full_response:
                self._append_history(
                    f"[bold green]Agent:[/bold green] {full_response}",
                    f"Agent: {full_response}",
                )
            if _agent_error is not None:
                self._append_history(
                    f"[bold red]Error:[/bold red] {_agent_error}",
                    f"Error: {_agent_error}",
                )
            self._stream_buf.clear()
            self.is_agent_busy = False
            self.post_message(ChatPanel.AgentDone())

    # ── helpers ───────────────────────────────────────────────────────────────

    def _append_history(self, rich_line: str, plain_line: str, *, is_trace: bool = False) -> None:
        """Write a line to the history RichLog and the plain transcript."""
        self._history_rich.append((rich_line, is_trace))
        if not is_trace or self._show_traces:
            self.query_one("#history", RichLog).write(rich_line)
        if plain_line:
            self._transcript_lines.append(plain_line)

    def _rerender_history(self) -> None:
        """Clear and rewrite history — used for trace toggle and resize reflow."""
        history = self.query_one("#history", RichLog)
        history.clear()
        for markup, is_trace in self._history_rich:
            if not is_trace or self._show_traces:
                history.write(markup)

    def toggle_traces(self) -> None:
        """Show or hide agent tool-call trace lines."""
        self._show_traces = not self._show_traces
        self._rerender_history()
        state = "shown" if self._show_traces else "hidden"
        self.notify(f"Tool traces {state}", timeout=2)

    def _append_streaming(self, rich_line: str, plain_line: str) -> None:
        streaming = self.query_one("#streaming", RichLog)
        streaming.write(rich_line)
        if plain_line:
            self._stream_buf.append(plain_line)

    # ── resize reflow ─────────────────────────────────────────────────────────

    def on_resize(self) -> None:
        """Reflow history text when the pane width changes."""
        if self._history_rich:
            self._rerender_history()

    # ── stop action ───────────────────────────────────────────────────────────

    def action_stop_agent(self) -> None:
        """Cancel the running agent worker (Esc)."""
        self.workers.cancel_all()
        self.is_agent_busy = False
        streaming = self.query_one("#streaming", RichLog)
        streaming.remove_class("visible")
