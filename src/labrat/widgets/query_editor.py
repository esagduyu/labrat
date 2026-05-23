"""SQL editor widget with syntax highlighting, line numbers, and cursor tracking."""

from typing import ClassVar

from textual.binding import Binding, BindingType
from textual.message import Message
from textual.widgets import TextArea
from textual.widgets.text_area import Location


class QueryEditor(TextArea):
    """TextArea subclass configured as a SQL editor.

    Defaults: SQL language highlighting, line numbers, indent-on-tab, no soft wrap.
    Posts QueryEditor.CursorMoved whenever the cursor moves.
    Adds Ctrl+/ to toggle SQL -- comments on the current or selected lines.
    """

    class CursorMoved(Message):
        """Posted when the cursor location changes (1-indexed row, col)."""

        def __init__(self, row: int, col: int) -> None:
            self.row = row
            self.col = col
            super().__init__()

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+slash", "toggle_comment", "Toggle Comment", show=True),
        Binding("ctrl+a", "select_all", "Select All", priority=True),
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

    # ── cursor tracking ───────────────────────────────────────────────────────

    def on_text_area_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        row, col = event.selection.end
        self.post_message(QueryEditor.CursorMoved(row + 1, col + 1))

    # ── comment toggle ────────────────────────────────────────────────────────

    def action_toggle_comment(self) -> None:
        """Toggle SQL -- comment on the current or selected lines."""
        sel = self.selection
        start_row = min(sel.start[0], sel.end[0])
        end_row = max(sel.start[0], sel.end[0])

        # Determine if we're adding or removing comments:
        # remove if ALL selected lines start with --; otherwise add.
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
        # End at a large column; get_text_range clips to line end
        end: Location = (row, 10_000)
        return self.get_text_range(start, end)
