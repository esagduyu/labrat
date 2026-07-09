"""HarvestReviewScreen: human approval gate for harvested Scent sections (M5).

Drafts arrive domain-keyed; every row starts APPROVED (the human deselects).
Apply routes each approved section to its domain doc via
apply_approved_sections — which audits fail-loud BEFORE writing. A
contamination hit renders in the status line and nothing further is written.

Caveat: apply_approved_sections audits per domain doc, so when approved
sections span multiple domains, a contamination hit in a later domain can
land after an earlier domain has already been written (the single-domain
audit test in this module's test suite doesn't hit this case). This matches
the shipped per-doc audit contract; approved-but-unwritten domains simply
remain draftable on the next harvest, since apply is idempotent via body
dedup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label, Static

if TYPE_CHECKING:
    from labrat.maze.document import Section
    from labrat.maze.store import MazeStore

_APPROVED = "✓ apply"
_SKIPPED = "· skip"


class HarvestReviewScreen(ModalScreen[int]):
    """Review drafted Scent sections; dismisses with the applied count."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Close", show=True),
        Binding("space", "toggle_row", "Approve/skip", show=True),
    ]

    DEFAULT_CSS = """
    HarvestReviewScreen { align: center middle; }
    HarvestReviewScreen > Vertical {
        width: 76; height: 20;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    HarvestReviewScreen #drafts-table { height: 1fr; }
    HarvestReviewScreen #actions { height: auto; margin-top: 1; }
    HarvestReviewScreen Button { margin: 0 1; min-width: 16; }
    HarvestReviewScreen #status { color: $text-muted; }
    """

    def __init__(self, drafts: dict[str, list[Section]], store: MazeStore) -> None:
        super().__init__()
        self._drafts = drafts
        self._store = store
        # Flat row model: (cluster_key, section); row key = str(index).
        self._rows: list[tuple[str, Section]] = [
            (key, s) for key in sorted(drafts) for s in drafts[key]
        ]
        self._approved: dict[int, bool] = {i: True for i in range(len(self._rows))}

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                "[bold]─ Harvested Learnings · review before writing to Scent ─[/bold]",
                id="title",
                markup=True,
            )
            yield DataTable(id="drafts-table", cursor_type="row")
            with Horizontal(id="actions"):
                yield Button("Apply approved", id="apply-btn", variant="primary")
                yield Button("Cancel  [Esc]", id="close-btn")
            yield Label("", id="status")

    def on_mount(self) -> None:
        table = self.query_one("#drafts-table", DataTable)
        table.add_columns("Apply?", "Domain", "Section", "Preview")
        from labrat.screens.harvest_controller import domain_for_cluster

        for i, (key, section) in enumerate(self._rows):
            preview = section.body.replace("\n", " ")[:60]
            table.add_row(_APPROVED, domain_for_cluster(key), section.heading, preview, key=str(i))

    def action_toggle_row(self) -> None:
        table = self.query_one("#drafts-table", DataTable)
        if table.row_count == 0:
            return
        row = table.cursor_row
        key = table.coordinate_to_cell_key(Coordinate(row, 0)).row_key
        idx = int(str(key.value))
        self._approved[idx] = not self._approved[idx]
        table.update_cell_at(Coordinate(row, 0), _APPROVED if self._approved[idx] else _SKIPPED)

    @on(Button.Pressed, "#apply-btn")
    def action_apply(self) -> None:
        from labrat.maze.harvest import apply_approved_sections
        from labrat.maze.scent_audit import ScentContaminationError
        from labrat.screens.harvest_controller import domain_for_cluster

        by_domain: dict[str, list[Section]] = {}
        for i, (key, section) in enumerate(self._rows):
            if self._approved[i]:
                by_domain.setdefault(domain_for_cluster(key), []).append(section)
        applied = 0
        try:
            for domain, sections in sorted(by_domain.items()):
                apply_approved_sections(self._store, domain, sections)
                applied += len(sections)
        except ScentContaminationError as exc:
            # Fail-loud: show the audit verdict, write nothing further, stay open.
            self.query_one("#status", Label).update(
                f"[red]Blocked by contamination audit: {exc}[/red]"
            )
            return
        self.dismiss(applied)

    @on(Button.Pressed, "#close-btn")
    def action_cancel(self) -> None:
        self.dismiss(0)
