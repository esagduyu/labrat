"""SQL editor widget with syntax highlighting, line numbers, and cursor tracking."""

from __future__ import annotations

import re
from typing import Any, ClassVar

from textual import events
from textual.binding import Binding, BindingType
from textual.message import Message
from textual.widgets import TextArea
from textual.widgets.text_area import Location


class QueryEditor(TextArea):
    """TextArea subclass configured as a SQL editor.

    Defaults: SQL language highlighting, line numbers, indent-on-tab, no soft wrap.
    Posts QueryEditor.CursorMoved whenever the cursor moves.
    Adds Ctrl+/ to toggle SQL -- comments on the current or selected lines.
    Adds Ctrl+Enter to run the current query (or selection).
    Wires SQLCompleter for inline ghost-text suggestions (Tab to accept).
    """

    class CursorMoved(Message):
        """Posted when the cursor location changes (1-indexed row, col)."""

        def __init__(self, row: int, col: int) -> None:
            self.row = row
            self.col = col
            super().__init__()

    class RunRequested(Message):
        """Posted when the user triggers Run SQL (Ctrl+Enter)."""

        def __init__(self, sql: str) -> None:
            super().__init__()
            self.sql = sql

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+slash", "toggle_comment", "Toggle Comment", show=True),
        Binding("ctrl+a", "select_all", "Select All", priority=True),
        Binding("ctrl+enter", "run_sql", "Run", show=True, priority=True),
    ]

    def __init__(
        self,
        text: str = "",
        *,
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            text,
            language="sql",
            theme="monokai",
            soft_wrap=False,
            tab_behavior="indent",
            show_line_numbers=True,
            id=id,
            classes=classes,
            disabled=disabled,
        )
        self._completer: Any = None

    def set_completer(self, completer: Any) -> None:
        """Wire an SQLCompleter for ghost-text autocomplete."""
        self._completer = completer

    # ── cursor tracking ───────────────────────────────────────────────────────

    def on_text_area_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        row, col = event.selection.end
        self.post_message(QueryEditor.CursorMoved(row + 1, col + 1))

    # ── autocomplete (ghost text) ─────────────────────────────────────────────

    async def _on_key(self, event: events.Key) -> None:
        """Intercept Tab to accept suggestion; fall through for normal indent."""
        if event.key == "tab" and self.suggestion:
            self.insert(self.suggestion)
            self.suggestion = ""
            event.prevent_default()
            event.stop()
            return
        await super()._on_key(event)

    def update_suggestion(self) -> None:
        if self._completer is None:
            self.suggestion = ""
            return
        text = self.text
        cursor_row, cursor_col = self.cursor_location
        lines = text.split("\n")
        offset = sum(len(lines[i]) + 1 for i in range(cursor_row)) + cursor_col
        completions = self._completer.complete(text, offset)
        if not completions:
            self.suggestion = ""
            return
        top = completions[0].label
        prefix = _word_at_cursor(text, offset)
        if prefix and top.lower().startswith(prefix.lower()):
            self.suggestion = top[len(prefix) :]
        else:
            self.suggestion = ""

    # ── run action ────────────────────────────────────────────────────────────

    def action_run_sql(self) -> None:
        sql = (self.selected_text or self.text).strip()
        if sql:
            self.post_message(QueryEditor.RunRequested(sql))

    # ── comment toggle ────────────────────────────────────────────────────────

    def action_toggle_comment(self) -> None:
        """Toggle SQL -- comment on the current or selected lines."""
        sel = self.selection
        start_row = min(sel.start[0], sel.end[0])
        end_row = max(sel.start[0], sel.end[0])

        lines = [self._line_text(r) for r in range(start_row, end_row + 1)]
        removing = all(ln.startswith("--") for ln in lines)

        for row_idx in range(start_row, end_row + 1):
            line = self._line_text(row_idx)
            if removing:
                new_line = line[3:] if line.startswith("-- ") else line[2:]
            else:
                new_line = "-- " + line
            end_col = len(line)
            self.replace(new_line, start=(row_idx, 0), end=(row_idx, end_col))

    def _line_text(self, row: int) -> str:
        """Return the text of line *row* (0-indexed) without the trailing newline."""
        start: Location = (row, 0)
        end: Location = (row, 10_000)
        return self.get_text_range(start, end)


def _word_at_cursor(text: str, offset: int) -> str:
    """Extract the identifier token immediately before the cursor (for suggestion suffix)."""
    prefix = text[:offset]
    m = re.search(r"[\w.]+$", prefix)
    return m.group(0) if m else ""
