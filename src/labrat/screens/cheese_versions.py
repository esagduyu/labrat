"""CheeseVersionsScreen: browse Cheese version history, re-share, rollback (Cheese v1 Task 6)."""

from __future__ import annotations

from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static


class CheeseVersionsScreen(ModalScreen[None]):
    """Modal: every Cheese's version history — re-share a version or roll back."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Close", show=True),
        Binding("r", "rollback_selected", "Rollback", show=True),
    ]

    DEFAULT_CSS = """
    CheeseVersionsScreen { align: center middle; }
    CheeseVersionsScreen > Vertical {
        width: 90;
        height: 30;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    CheeseVersionsScreen #title { margin-bottom: 1; }
    CheeseVersionsScreen #versions-list { height: 1fr; }
    CheeseVersionsScreen #status { margin-top: 1; color: $text-muted; }
    """

    def __init__(self) -> None:
        super().__init__()
        import labrat.cheese.store as cheese_store_mod
        from labrat.cheese.store import CheeseStore

        self._store = CheeseStore(cheese_store_mod.DEFAULT_CHEESE_ROOT)
        # Flat (cheese_id, version_n) rows, one per ListView item, in display order.
        self._rows: list[tuple[str, int]] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("[bold]─ Cheese Versions ─[/bold]", id="title", markup=True)
            yield ListView(id="versions-list")
            yield Label("", id="status")

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        list_view = self.query_one("#versions-list", ListView)
        list_view.clear()
        self._rows = []
        for manifest in self._store.list_cheeses():
            for v in manifest.versions:
                marker = "  ← current" if v.n == manifest.current else ""
                label = (
                    f"{manifest.title[:30] or '(untitled)'}  ·  v{v.n} · "
                    f"{v.exported_at:%Y-%m-%d %H:%M} · rows:{v.rows_mode}{marker}"
                )
                list_view.append(ListItem(Label(label)))
                self._rows.append((manifest.cheese_id, v.n))
        if self._rows:
            list_view.index = 0
        self.query_one("#status", Label).update("" if self._rows else "No Cheese exports yet.")

    def _selected_row(self) -> tuple[str, int] | None:
        list_view = self.query_one("#versions-list", ListView)
        idx = list_view.index
        if idx is None or not (0 <= idx < len(self._rows)):
            return None
        return self._rows[idx]

    @on(ListView.Selected, "#versions-list")
    def _on_selected(self, event: ListView.Selected) -> None:
        row = self._selected_row()
        if row is None:
            return
        cheese_id, n = row
        path = self._store.version_path(cheese_id, n)
        self.query_one("#status", Label).update(f"→ {path}")
        self.notify(str(path), timeout=6)

    def action_rollback_selected(self) -> None:
        row = self._selected_row()
        if row is None:
            self.query_one("#status", Label).update("No version selected.")
            return
        cheese_id, n = row
        try:
            self._store.rollback(cheese_id, n)
        except Exception as exc:  # never raise into the TUI
            self.notify(f"Rollback failed: {exc}", severity="error", timeout=8)
            return
        self._refresh()
        self.query_one("#status", Label).update(f"Rolled back → v{n}")
        self.notify(f"Rolled back to v{n}", timeout=4)

    def action_cancel(self) -> None:
        self.dismiss(None)
