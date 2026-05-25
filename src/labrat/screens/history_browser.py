"""HistoryBrowserScreen: browse query history and re-run in the editor (M28)."""

from __future__ import annotations

from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label, Static


class HistoryBrowserScreen(ModalScreen[str | None]):
    """Query history modal.  Dismisses with the SQL to load, or None."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Close", show=True),
        Binding("enter", "load_selected", "Load into editor", show=True),
    ]

    DEFAULT_CSS = """
    HistoryBrowserScreen { align: center middle; }
    HistoryBrowserScreen > Vertical {
        width: 84;
        height: 30;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    HistoryBrowserScreen #title { margin-bottom: 1; }
    HistoryBrowserScreen #history-table { height: 1fr; }
    HistoryBrowserScreen #actions { height: auto; margin-top: 1; }
    HistoryBrowserScreen Button { margin: 0 1; min-width: 18; }
    HistoryBrowserScreen #status { margin-top: 1; color: $text-muted; }
    """

    def __init__(self, profile_name: str) -> None:
        super().__init__()
        self._profile = profile_name
        from labrat.history.log import QueryHistoryLog

        self._log = QueryHistoryLog()

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("[bold]─ Query History ─[/bold]", id="title", markup=True)
            yield DataTable(id="history-table", cursor_type="row")
            with Horizontal(id="actions"):
                yield Button("Load into editor  [Enter]", id="load-btn", variant="primary")
                yield Button("Close  [Esc]", id="close-btn")
            yield Label("", id="status")

    def on_mount(self) -> None:
        self._refresh_table()

    def _refresh_table(self) -> None:
        table = self.query_one("#history-table", DataTable)
        table.clear(columns=True)
        table.add_columns("When", "SQL", "Rows", "OK")
        events = self._log.read_profile(self._profile)
        events = list(reversed(events))[:200]
        for ev in events:
            sql_preview = ev.sql_final[:60].replace("\n", " ")
            if len(ev.sql_final) > 60:
                sql_preview += "…"
            ok_mark = "✓" if ev.success else "✗"
            rows = str(ev.row_count) if ev.row_count is not None else "—"
            table.add_row(
                ev.timestamp.strftime("%m-%d %H:%M"),
                sql_preview,
                rows,
                ok_mark,
                key=ev.sql_final,
            )
        count = len(events)
        self.query_one("#status", Label).update(
            f"Showing {count} recent {'query' if count == 1 else 'queries'}."
        )

    def _selected_sql(self) -> str | None:
        from textual.coordinate import Coordinate

        table = self.query_one("#history-table", DataTable)
        if table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key
        return str(key.value) if key and key.value is not None else None

    @on(Button.Pressed, "#load-btn")
    def action_load_selected(self) -> None:
        sql = self._selected_sql()
        if sql:
            self.dismiss(sql)

    @on(Button.Pressed, "#close-btn")
    def action_cancel(self) -> None:
        self.dismiss(None)
