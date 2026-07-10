"""FindingsViewerScreen: browse, delete, and Cheese-export pinned findings (M18, Cheese v1)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label, Static

if TYPE_CHECKING:
    from labrat.thread.model import Finding


class FindingsViewerScreen(ModalScreen[None]):
    """Modal to view, delete, and Cheese-export pinned findings."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Close", show=True),
        Binding("d", "delete_selected", "Delete", show=True),
        Binding("e", "export_html", "Export Report", show=True),
        Binding("x", "export_selected", "Export Finding", show=True),
        Binding("E", "export_html_no_rows", "Report (no rows)", show=False),
        Binding("X", "export_selected_no_rows", "Finding (no rows)", show=False),
        Binding("v", "cheese_versions", "Versions", show=True),
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
                yield Button("Export Report  [E]", id="export-btn", variant="primary")
                yield Button("Export Finding  [X]", id="export-selected-btn")
                yield Button("Versions  [V]", id="versions-btn")
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

    def _export(
        self,
        findings: list[Finding],
        *,
        kind: Literal["single", "report"],
        title: str,
        rows_mode: Literal["preview", "none"],
    ) -> None:
        import labrat.cheese.store as cheese_store_mod
        from labrat.cheese.export import export_cheese
        from labrat.cheese.store import CheeseStore, FindingDataStore

        if not findings:
            self.query_one("#status", Label).update("No findings to export.")
            return
        try:
            path = export_cheese(
                findings,
                kind=kind,
                title=title,
                rows_mode=rows_mode,
                cheese_store=CheeseStore(cheese_store_mod.DEFAULT_CHEESE_ROOT),
                data_store=FindingDataStore(cheese_store_mod.DEFAULT_DATA_ROOT),
            )
        except Exception as exc:  # never raise into the TUI
            self.notify(f"Cheese export failed: {exc}", severity="error", timeout=8)
            return
        self.query_one("#status", Label).update(f"Exported → {path}")
        self.notify(f"\U0001f9c0 Cheese exported: {path}", timeout=6)

    def _selected_finding(self) -> Finding | None:
        fid = self._selected_finding_id()
        if fid is None:
            return None
        return next((f for f in self._mgr.list_findings() if f.id == fid), None)

    @on(Button.Pressed, "#export-btn")
    def action_export_html(self) -> None:
        self._export(
            list(self._mgr.list_findings()),
            kind="report",
            title="LabRat Report",
            rows_mode="preview",
        )

    def action_export_html_no_rows(self) -> None:
        self._export(
            list(self._mgr.list_findings()),
            kind="report",
            title="LabRat Report",
            rows_mode="none",
        )

    @on(Button.Pressed, "#export-selected-btn")
    def action_export_selected(self) -> None:
        finding = self._selected_finding()
        if finding is None:
            self.query_one("#status", Label).update("No finding selected.")
            return
        self._export([finding], kind="single", title=finding.question, rows_mode="preview")

    def action_export_selected_no_rows(self) -> None:
        finding = self._selected_finding()
        if finding is None:
            self.query_one("#status", Label).update("No finding selected.")
            return
        self._export([finding], kind="single", title=finding.question, rows_mode="none")

    @on(Button.Pressed, "#versions-btn")
    def action_cheese_versions(self) -> None:
        from labrat.screens.cheese_versions import CheeseVersionsScreen

        self.app.push_screen(CheeseVersionsScreen())

    @on(Button.Pressed, "#close-btn")
    def action_cancel(self) -> None:
        self.dismiss(None)
