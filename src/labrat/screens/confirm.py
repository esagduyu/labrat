"""ConfirmScreen: generic yes/no modal (used for M13 mutation gates)."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmScreen(ModalScreen[bool]):
    """Modal that asks a yes/no question and returns True (yes) or False (no)."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss_no", "", show=False),
        Binding("n", "dismiss_no", "", show=False),
        Binding("y", "confirm_yes", "", show=False),
    ]

    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    ConfirmScreen > Vertical {
        width: 60;
        height: auto;
        border: thick $warning;
        background: $surface;
        padding: 1 2;
    }
    ConfirmScreen Static { margin-bottom: 1; }
    ConfirmScreen Horizontal { height: auto; align: center middle; }
    ConfirmScreen Button { margin: 0 1; min-width: 12; }
    """

    def __init__(self, question: str) -> None:
        super().__init__()
        self._question = question

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self._question, markup=True)
            with Horizontal():
                yield Button("Run anyway  [Y]", id="yes", variant="error")
                yield Button("Cancel  [N/Esc]", id="no", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_dismiss_no(self) -> None:
        self.dismiss(False)

    def action_confirm_yes(self) -> None:
        self.dismiss(True)
