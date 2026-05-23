"""Main Textual application for LabRat."""

from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.widgets import Static

from labrat.branding import get_banner_renderable


class LabRatApp(App[None]):
    """LabRat — terminal-native data agent."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Static(get_banner_renderable("splash"))
