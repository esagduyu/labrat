"""ThreadManagerScreen: create, rename, and switch threads (M17)."""

from __future__ import annotations

from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, Static


class ThreadManagerScreen(ModalScreen[str | None]):
    """Thread list modal.

    Dismisses with the ID of the thread to switch to, or None to keep the
    current thread.  Creating a new thread immediately switches to it.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Close", show=True),
        Binding("enter", "switch_selected", "Switch", show=True),
    ]

    DEFAULT_CSS = """
    ThreadManagerScreen { align: center middle; }
    ThreadManagerScreen > Vertical {
        width: 72;
        height: 28;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    ThreadManagerScreen #title { margin-bottom: 1; }
    ThreadManagerScreen #thread-table { height: 1fr; }
    ThreadManagerScreen #rename-row {
        height: 3;
        display: none;
    }
    ThreadManagerScreen #rename-row.visible { display: block; }
    ThreadManagerScreen #actions { height: auto; margin-top: 1; }
    ThreadManagerScreen Button { margin: 0 1; min-width: 10; }
    ThreadManagerScreen #status { margin-top: 1; color: $text-muted; }
    """

    def __init__(self, profile_name: str, current_thread_id: str | None = None) -> None:
        super().__init__()
        self._profile = profile_name
        self._current_id = current_thread_id
        from labrat.thread.manager import ThreadManager

        self._mgr = ThreadManager()

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("[bold]─ Thread Manager ─[/bold]", id="title", markup=True)
            yield DataTable(id="thread-table", cursor_type="row")
            with Vertical(id="rename-row"):
                yield Input(placeholder="New thread name…", id="rename-input")
            with Horizontal(id="actions"):
                yield Button("New", id="new-btn", variant="primary")
                yield Button("Switch  [Enter]", id="switch-btn")
                yield Button("Rename", id="rename-btn")
                yield Button("Close  [Esc]", id="close-btn")
            yield Label("", id="status")

    def on_mount(self) -> None:
        self._refresh_table()

    def _refresh_table(self) -> None:
        table = self.query_one("#thread-table", DataTable)
        table.clear(columns=True)
        table.add_columns("", "Name", "Profile", "Created")
        for thread in self._mgr.list_threads():
            active = "●" if thread.id == self._current_id else " "
            table.add_row(
                active,
                thread.name,
                thread.profile_name,
                thread.created_at.strftime("%Y-%m-%d %H:%M"),
                key=thread.id,
            )

    def _selected_thread_id(self) -> str | None:
        table = self.query_one("#thread-table", DataTable)
        if table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key
        return str(key.value) if key and key.value is not None else None

    @on(Button.Pressed, "#new-btn")
    def _new_thread(self) -> None:
        import uuid

        name = f"thread-{uuid.uuid4().hex[:6]}"
        t = self._mgr.create_thread(name=name, profile_name=self._profile)
        self._current_id = t.id
        self._refresh_table()
        self.query_one("#status", Label).update(f'Created "{name}" - switching to it.')
        self.dismiss(t.id)

    @on(Button.Pressed, "#switch-btn")
    def action_switch_selected(self) -> None:
        tid = self._selected_thread_id()
        if tid:
            self.dismiss(tid)

    @on(Button.Pressed, "#rename-btn")
    def _toggle_rename(self) -> None:
        row = self.query_one("#rename-row")
        row.toggle_class("visible")
        if "visible" in row.classes:
            self.query_one("#rename-input", Input).focus()

    @on(Input.Submitted, "#rename-input")
    def _apply_rename(self, event: Input.Submitted) -> None:
        new_name = event.value.strip()
        if not new_name:
            return
        tid = self._selected_thread_id() or self._current_id
        if tid is None:
            return
        threads = self._mgr._store.load_threads()
        for t in threads:
            if t.id == tid:
                updated = t.model_copy(update={"name": new_name})
                self._mgr._store.replace_thread(updated)
                break
        self.query_one("#rename-row").remove_class("visible")
        self.query_one("#rename-input", Input).value = ""
        self._refresh_table()
        self.query_one("#status", Label).update(f'Renamed to "{new_name}".')

    @on(Button.Pressed, "#close-btn")
    def action_cancel(self) -> None:
        self.dismiss(None)


# We need Coordinate — import at module level to avoid repeated imports
from textual.coordinate import Coordinate  # noqa: E402
