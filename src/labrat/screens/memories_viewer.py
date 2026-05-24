"""MemoriesViewerScreen: view and delete agent memories (M31)."""

from __future__ import annotations

from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label, Static


class MemoriesViewerScreen(ModalScreen[None]):
    """Modal to view and delete stored agent memories for the current profile."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Close", show=True),
        Binding("d", "delete_selected", "Delete", show=True),
    ]

    DEFAULT_CSS = """
    MemoriesViewerScreen { align: center middle; }
    MemoriesViewerScreen > Vertical {
        width: 80;
        height: 30;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    MemoriesViewerScreen #title { margin-bottom: 1; }
    MemoriesViewerScreen #memories-table { height: 1fr; }
    MemoriesViewerScreen #actions { height: auto; margin-top: 1; }
    MemoriesViewerScreen Button { margin: 0 1; min-width: 14; }
    MemoriesViewerScreen #status { margin-top: 1; color: $text-muted; }
    """

    def __init__(self, profile_name: str) -> None:
        super().__init__()
        self._profile = profile_name
        from labrat.memory.store import MemoryStore

        self._store = MemoryStore()

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("[bold]─ Agent Memories ─[/bold]", id="title", markup=True)
            yield DataTable(id="memories-table", cursor_type="row")
            with Horizontal(id="actions"):
                yield Button("Delete  [D]", id="delete-btn", variant="error")
                yield Button("Close  [Esc]", id="close-btn")
            yield Label("", id="status")

    def on_mount(self) -> None:
        self._refresh_table()

    def _refresh_table(self) -> None:
        table = self.query_one("#memories-table", DataTable)
        table.clear(columns=True)
        table.add_columns("Memory", "Scope", "Kind", "Used")
        memories = self._store.read_profile(self._profile)
        for m in memories:
            text_preview = m.text[:55] + ("…" if len(m.text) > 55 else "")
            table.add_row(
                text_preview,
                m.scope.value,
                m.kind.value,
                str(m.application_count),
                key=m.id,
            )
        count = len(memories)
        self.query_one("#status", Label).update(
            f'{count} {"memory" if count == 1 else "memories"} stored for profile "{self._profile}".'
        )

    def _selected_memory_id(self) -> str | None:
        from textual.coordinate import Coordinate

        table = self.query_one("#memories-table", DataTable)
        if table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(
            Coordinate(table.cursor_row, 0)
        ).row_key
        return str(key.value) if key and key.value is not None else None

    @on(Button.Pressed, "#delete-btn")
    def action_delete_selected(self) -> None:
        mid = self._selected_memory_id()
        if not mid:
            return
        self._store.delete(self._profile, mid)
        self._refresh_table()
        self.query_one("#status", Label).update("Memory deleted.")

    @on(Button.Pressed, "#close-btn")
    def action_cancel(self) -> None:
        self.dismiss(None)
