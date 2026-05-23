"""TraceLog: alternative action-trace view of agent tool calls (M16)."""

from __future__ import annotations

import json
from typing import Any

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import RichLog


class TraceLog(Widget):
    """Scrolling log that records agent tool calls and text in arrival order.

    Used as an alternative (non-conversational) view of what the agent is doing.
    Each entry is appended as a line; the full accumulated text is accessible via
    the ``content`` property.
    """

    DEFAULT_CSS = """
    TraceLog {
        height: 1fr;
        border: solid $surface;
    }
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._lines: list[str] = []

    def compose(self) -> ComposeResult:
        yield RichLog(id="trace-log", highlight=True, markup=True, wrap=True)

    @property
    def content(self) -> str:
        """Full trace as a single string (lines joined by newlines)."""
        return "\n".join(self._lines)

    def log_tool_call(self, name: str, args: dict[str, Any], result: str) -> None:
        """Append a tool invocation record to the trace."""
        args_str = json.dumps(args, separators=(",", ":"))
        line = f"[dim]▸[/dim] [bold]{name}[/bold]({args_str}) → {result}"
        self._lines.append(f"▸ {name}({args_str}) → {result}")
        self.query_one("#trace-log", RichLog).write(line)

    def log_text(self, text: str) -> None:
        """Append agent text to the trace."""
        self._lines.append(text)
        self.query_one("#trace-log", RichLog).write(text)
