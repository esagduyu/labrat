"""RecordDecisionScreen: capture an analyst-typed decision (Decision-trail v1).

Simplest possible modal — a single free-text TextArea plus Save/Cancel —
mirroring TrailReviewScreen's/HarvestReviewScreen's structure (Static title,
Vertical body, Horizontal actions row, status Label). No LLM involved: the
typed text is handed back to the caller verbatim via ``dismiss``; MainScreen
owns building the ``Memory`` and appending it to the store (see
``action_record_decision`` in ``screens/main.py``).
"""

from __future__ import annotations

from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TextArea


class RecordDecisionScreen(ModalScreen[str | None]):
    """Prompt for a decision; dismisses with the typed text (None on cancel)."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    DEFAULT_CSS = """
    RecordDecisionScreen { align: center middle; }
    RecordDecisionScreen > Vertical {
        width: 70; height: 16;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    RecordDecisionScreen #hint { color: $text-muted; margin-bottom: 1; }
    RecordDecisionScreen #decision-text { height: 1fr; }
    RecordDecisionScreen #actions { height: auto; margin-top: 1; }
    RecordDecisionScreen Button { margin: 0 1; min-width: 16; }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("[bold]─ Record a Decision ─[/bold]", id="title", markup=True)
            yield Static("What did you decide, and why? This is captured verbatim.", id="hint")
            yield TextArea("", id="decision-text")
            with Horizontal(id="actions"):
                yield Button("Save  [Ctrl+S]", id="save-btn", variant="primary")
                yield Button("Cancel  [Esc]", id="close-btn")

    @on(Button.Pressed, "#save-btn")
    def action_save(self) -> None:
        text = self.query_one("#decision-text", TextArea).text
        self.dismiss(text)

    @on(Button.Pressed, "#close-btn")
    def action_cancel(self) -> None:
        self.dismiss(None)
