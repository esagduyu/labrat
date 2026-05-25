"""HelpScreen: keyboard shortcuts modal (M5)."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

_SECTIONS = [
    (
        "Navigation",
        [
            ("Ctrl+1", "Focus chat panel"),
            ("Ctrl+2", "Focus editor"),
            ("Ctrl+3", "Focus results table"),
            ("Ctrl+4", "Focus schema browser"),
            ("Ctrl+H", "Toggle schema pane"),
            ("Ctrl+L", "Toggle chat panel"),
            ("E", "Expand / collapse all schema nodes"),
        ],
    ),
    (
        "Editor",
        [
            ("Ctrl+Enter", "Run SQL (or selected text)"),
            ("Ctrl+/", "Toggle SQL comment"),
            ("Tab", "Accept autocomplete suggestion"),
        ],
    ),
    (
        "Results table",
        [
            ("P", "Pin current result as a Finding"),
            ("S", "Sort by focused column"),
            ("Ctrl+C", "Copy focused cell"),
        ],
    ),
    (
        "Session",
        [
            ("Ctrl+T", "Thread manager — create / rename / switch"),
            ("Ctrl+K", "Pinned findings — view / delete / export HTML"),
            ("Ctrl+R", "Query history — browse and reload past queries"),
            ("Ctrl+G", "Agent memories — view and delete"),
        ],
    ),
    (
        "Chat",
        [
            ("Enter", "Send message"),
            ("Escape", "Stop agent"),
            ("Ctrl+\\", "Toggle tool-call traces"),
        ],
    ),
    (
        "App",
        [
            ("?  /  F1", "This help screen"),
            ("Q", "Quit"),
        ],
    ),
]


def _build_help_text() -> str:
    parts: list[str] = []
    for section, bindings in _SECTIONS:
        parts.append(f"[bold]─ {section} {'─' * (30 - len(section))}[/bold]")
        for key, desc in bindings:
            parts.append(f"  [bold cyan]{key:<20}[/bold cyan] {desc}")
        parts.append("")
    return "\n".join(parts).rstrip()


class HelpScreen(ModalScreen[None]):
    """Keyboard shortcuts reference popup (dismiss with ?, Esc, or F1)."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape,question_mark,f1", "dismiss", "Close"),
    ]

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    HelpScreen > Vertical {
        width: 60;
        height: auto;
        max-height: 90%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        header = "[bold]LabRat — Keyboard Shortcuts[/bold]\n"
        footer = (
            "\n[dim]Press [bold]?[/bold], [bold]F1[/bold], or [bold]Escape[/bold] to close[/dim]"
        )
        with Vertical():
            yield Static(header + "\n" + _build_help_text() + footer, markup=True)
