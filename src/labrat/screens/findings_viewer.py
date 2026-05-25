"""FindingsViewerScreen: browse, delete, and export pinned findings (M18, M20)."""

from __future__ import annotations

from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label, Static


class FindingsViewerScreen(ModalScreen[None]):
    """Modal to view, delete, and export pinned findings."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Close", show=True),
        Binding("d", "delete_selected", "Delete", show=True),
        Binding("e", "export_html", "Export HTML", show=True),
    ]

    DEFAULT_CSS = """
    FindingsViewerScreen { align: center middle; }
    FindingsViewerScreen > Vertical {
        width: 80;
        height: 30;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    FindingsViewerScreen #title { margin-bottom: 1; }
    FindingsViewerScreen #findings-table { height: 1fr; }
    FindingsViewerScreen #actions { height: auto; margin-top: 1; }
    FindingsViewerScreen Button { margin: 0 1; min-width: 14; }
    FindingsViewerScreen #status { margin-top: 1; color: $text-muted; }
    """

    def __init__(self) -> None:
        super().__init__()
        from labrat.thread.findings import FindingsManager

        self._mgr = FindingsManager()

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("[bold]─ Pinned Findings ─[/bold]", id="title", markup=True)
            yield DataTable(id="findings-table", cursor_type="row")
            with Horizontal(id="actions"):
                yield Button("Delete  [D]", id="delete-btn", variant="error")
                yield Button("Export HTML  [E]", id="export-btn", variant="primary")
                yield Button("Close  [Esc]", id="close-btn")
            yield Label("", id="status")

    def on_mount(self) -> None:
        self._refresh_table()

    def _refresh_table(self) -> None:
        table = self.query_one("#findings-table", DataTable)
        table.clear(columns=True)
        table.add_columns("#", "Question", "SQL preview", "Pinned at")
        for i, f in enumerate(self._mgr.list_findings(), 1):
            sql_preview = f.sql[:50].replace("\n", " ") + ("…" if len(f.sql) > 50 else "")
            table.add_row(
                str(i),
                f.question[:40] or "(no question)",
                sql_preview,
                f.pinned_at.strftime("%m-%d %H:%M"),
                key=f.id,
            )

    def _selected_finding_id(self) -> str | None:
        from textual.coordinate import Coordinate

        table = self.query_one("#findings-table", DataTable)
        if table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key
        return str(key.value) if key and key.value is not None else None

    @on(Button.Pressed, "#delete-btn")
    def action_delete_selected(self) -> None:
        fid = self._selected_finding_id()
        if not fid:
            return
        self._mgr.unpin(fid)
        self._refresh_table()
        self.query_one("#status", Label).update("Finding removed.")

    @on(Button.Pressed, "#export-btn")
    def action_export_html(self) -> None:
        findings = self._mgr.list_findings()
        if not findings:
            self.query_one("#status", Label).update("No findings to export.")
            return
        from labrat.audit.export import export_findings

        path = export_findings(findings)
        import subprocess
        import sys

        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            elif sys.platform.startswith("linux"):
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            pass
        self.query_one("#status", Label).update(f"Exported → {path}")

    @on(Button.Pressed, "#close-btn")
    def action_cancel(self) -> None:
        self.dismiss(None)
