"""TrailReviewScreen: human approval gate for a drafted Trail Scent doc (Trail v1).

Mirrors HarvestReviewScreen's audited-apply contract: the draft is already
contamination-audited at draft time (draft_trail_from_finding), but apply_trail
re-audits (fail-loud) before writing — belt-and-suspenders against an edit that
reintroduces contaminated text. A contamination hit on apply renders in the
status line and nothing is written; the screen stays open so the analyst can
edit and retry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static, TextArea

if TYPE_CHECKING:
    from pathlib import Path

    from labrat.maze.document import ScentDoc
    from labrat.maze.store import MazeStore

# Free-text sections the analyst can edit before saving; Reference SQL and
# Validations are derived/display-only (regenerating them from an edited body
# would be lossy, so they're shown read-only, same as the source SQL/rules).
_EDITABLE = {"When to use", "Steps", "Gotchas"}
_FIELD_IDS = {
    "When to use": "when-to-use",
    "Steps": "steps",
    "Reference SQL": "reference-sql",
    "Validations": "validations",
    "Gotchas": "gotchas",
}


class TrailReviewScreen(ModalScreen[str | None]):
    """Review a drafted Trail; dismisses with the applied domain slug (None on skip)."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Close", show=True),
        Binding("a", "approve", "Approve & save", show=True),
    ]

    DEFAULT_CSS = """
    TrailReviewScreen { align: center middle; }
    TrailReviewScreen > Vertical {
        width: 88; height: 34;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    TrailReviewScreen #body { height: 1fr; }
    TrailReviewScreen .heading { margin-top: 1; text-style: bold; }
    TrailReviewScreen TextArea { height: 6; }
    TrailReviewScreen .readonly { height: auto; max-height: 8; color: $text-muted; }
    TrailReviewScreen #actions { height: auto; margin-top: 1; }
    TrailReviewScreen Button { margin: 0 1; min-width: 20; }
    TrailReviewScreen #status { color: $text-muted; }
    """

    def __init__(self, doc: ScentDoc, store: MazeStore, *, git_root: Path | None = None) -> None:
        super().__init__()
        self._doc = doc
        self._store = store
        self._git_root = git_root

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(
                f"[bold]─ Save as Trail · {self._doc.domain} ─[/bold]", id="title", markup=True
            )
            with VerticalScroll(id="body"):
                for section in self._doc.sections:
                    yield Label(section.heading, classes="heading")
                    field_id = _FIELD_IDS.get(section.heading, section.heading.lower())
                    if section.heading in _EDITABLE:
                        yield TextArea(section.body, id=f"field-{field_id}")
                    else:
                        yield Static(section.body, id=f"field-{field_id}", classes="readonly")
            with Horizontal(id="actions"):
                yield Button("Approve & save  [A]", id="approve-btn", variant="primary")
                yield Button("Skip  [Esc]", id="close-btn")
            yield Label("", id="status")

    def _edited_doc(self) -> ScentDoc:
        sections = []
        for section in self._doc.sections:
            field_id = _FIELD_IDS.get(section.heading, section.heading.lower())
            if section.heading in _EDITABLE:
                body = self.query_one(f"#field-{field_id}", TextArea).text
            else:
                body = section.body
            sections.append(section.model_copy(update={"body": body}))
        return self._doc.model_copy(update={"sections": sections})

    @on(Button.Pressed, "#approve-btn")
    def action_approve(self) -> None:
        from labrat.maze.scent_audit import ScentContaminationError
        from labrat.maze.trail import apply_trail

        edited = self._edited_doc()
        try:
            apply_trail(self._store, edited, git_root=self._git_root)
        except ScentContaminationError as exc:
            self.query_one("#status", Label).update(
                f"[red]Draft blocked by contamination audit: {exc}[/red]"
            )
            return
        except Exception as exc:  # never raise into the TUI
            self.query_one("#status", Label).update(f"[red]Failed to save trail: {exc}[/red]")
            return
        self.notify(f"\U0001f97e Trail saved: {edited.domain}", timeout=6)
        self.dismiss(edited.domain)

    @on(Button.Pressed, "#close-btn")
    def action_cancel(self) -> None:
        self.dismiss(None)
