"""Main three-pane layout screen for LabRat."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widget import Widget

if TYPE_CHECKING:
    from labrat.db.base import Connection
    from labrat.db.catalog import Catalog


class _StatusBar(Widget):
    """One-row status bar: profile | dialect | thread | connection."""

    DEFAULT_CSS = """
    _StatusBar {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        *,
        profile: str = "—",
        dialect: str = "—",
        thread: str = "—",
        connected: bool = False,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._profile = profile
        self._dialect = dialect
        self._thread = thread
        self._connected = connected

    def render(self) -> str:
        status = "● connected" if self._connected else "○ disconnected"
        return (
            f" profile: {self._profile}"
            f"  dialect: {self._dialect}"
            f"  thread: {self._thread}"
            f"  {status}"
            f"  \U0001f400 LabRat"
        )


class _PaneHeader(Widget):
    """Single-row title strip for a pane."""

    DEFAULT_CSS = """
    _PaneHeader {
        height: 1;
        background: $panel-darken-1;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(self, title: str) -> None:
        super().__init__()
        self._title = title

    def render(self) -> str:
        return f"─ {self._title} ─"


class _PanePlaceholder(Widget):
    """Focusable placeholder content for a pane."""

    can_focus = True

    DEFAULT_CSS = """
    _PanePlaceholder {
        height: 1fr;
        color: $text-muted;
        padding: 1;
    }
    """

    def __init__(self, text: str, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._text = text

    def render(self) -> str:
        return self._text


class MainScreen(Screen[None]):
    """Three-pane layout: chat | editor + results | schema browser."""

    CSS_PATH = "../styles.tcss"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+1", "focus_chat", "Chat"),
        Binding("ctrl+2", "focus_editor", "Editor"),
        Binding("ctrl+3", "focus_results", "Results"),
        Binding("ctrl+4", "focus_schema", "Schema"),
        Binding("ctrl+h", "toggle_schema", "Toggle Schema"),
        Binding("ctrl+l", "toggle_chat", "Toggle Chat"),
    ]

    def __init__(
        self,
        *,
        profile: str = "—",
        dialect: str = "—",
        thread: str = "untitled",
        catalog: Catalog | None = None,
        connection: Connection | None = None,
    ) -> None:
        super().__init__()
        self._profile = profile
        self._dialect = dialect
        self._thread = thread
        self._catalog = catalog
        self._connection = connection

    def compose(self) -> ComposeResult:
        connected = self._connection is not None
        yield _StatusBar(
            profile=self._profile,
            dialect=self._dialect,
            thread=self._thread,
            connected=connected,
            id="status-top",
        )
        with Horizontal(id="main-split"):
            with Vertical(id="chat-pane"):
                yield _PaneHeader("chat")
                yield _PanePlaceholder("[ Chat Panel ]", id="chat-content")
            with Vertical(id="center-pane"):
                with Vertical(id="editor-pane"):
                    yield _PaneHeader("editor")
                    from labrat.widgets.query_editor import QueryEditor

                    yield QueryEditor(id="editor-content")
                with Vertical(id="results-pane"):
                    yield _PaneHeader("results")
                    yield _PanePlaceholder("[ Results ]", id="results-content")
            with Vertical(id="schema-pane"):
                yield _PaneHeader("schema")
                from labrat.widgets.schema_tree import SchemaBrowser

                browser = SchemaBrowser(
                    catalog=self._catalog,
                    profile_name=self._profile,
                    id="schema-content",
                )
                if self._connection is not None:
                    browser.set_connection(self._connection)
                yield browser
        yield _StatusBar(
            profile=self._profile,
            dialect=self._dialect,
            thread=self._thread,
            connected=connected,
            id="status-bottom",
        )

    def on_resize(self, event: events.Resize) -> None:
        """Enter narrow mode below 80 columns."""
        if event.size.width < 80:
            self.add_class("narrow")
        else:
            self.remove_class("narrow")

    # ── actions ──────────────────────────────────────────────────────────────

    def action_focus_chat(self) -> None:
        self.query_one("#chat-content").focus()

    def action_focus_editor(self) -> None:
        from labrat.widgets.query_editor import QueryEditor

        self.query_one("#editor-content", QueryEditor).focus()

    def action_focus_results(self) -> None:
        self.query_one("#results-content").focus()

    def action_focus_schema(self) -> None:
        self.query_one("#schema-content").query_one("#schema-tree").focus()

    def action_toggle_schema(self) -> None:
        schema = self.query_one("#schema-pane")
        schema.display = not schema.display

    def action_toggle_chat(self) -> None:
        chat = self.query_one("#chat-pane")
        chat.display = not chat.display
