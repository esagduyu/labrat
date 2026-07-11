"""Map v1 (domain bundles): TUI author/curate + additive-activation screens.

Two modals, mirroring the shipped Trail/Harvest review surfaces:

- ``MapActivateScreen`` — lists every ``kind="map"`` doc in the store with an
  active/inactive toggle (space, or click the row). Toggling mutates the
  caller-owned ``active_maps`` list **in place** (append/remove) so the live
  reference already handed to ``ToolContext`` in ``MainScreen`` sees the
  change immediately — this screen never reassigns that list. Additive:
  multiple Maps can be active at once. Only lists Maps that actually exist on
  disk, so it is impossible to "activate" a phantom slug. Also the entry
  point to create a new Map, curate an existing one, or trigger the
  Cartographer dbt auto-seed (delegated back to ``MainScreen`` — the seed
  needs the profile's dbt project path + manifest resolution it owns).

- ``MapEditScreen`` — create or curate a single Map: pick Scent + Trail
  members from the domains that already exist in the store (a Map is pure
  pointers — never freehand text for members), edit the free-text Overview
  and one-per-line Suggested Prompts, then save via an audited
  ``store.write_doc(kind="map")`` (mirrors ``TrailReviewScreen.action_approve``'s
  audit-then-write contract, generalized off ``kind="trail"``).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, Static, TextArea

if TYPE_CHECKING:
    from labrat.maze.document import ScentDoc
    from labrat.maze.store import MazeStore

_ACTIVE = "✓ active"
_INACTIVE = "· inactive"
_INCLUDED = "✓ member"
_EXCLUDED = "· —"


def _slugify(text: str) -> str:
    """Same ASCII-transliterate-then-slug convention as ``trail.py::intent_slug``,
    generalized off the Trail-specific fallback name."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug or "untitled-map"


def _parse_bullet_lines(text: str) -> list[str]:
    """One entry per non-blank line; tolerates a hand-typed leading '- '."""
    items: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        elif stripped.startswith("-"):
            stripped = stripped[1:].strip()
        if stripped:
            items.append(stripped)
    return items


def _overview_body(doc: ScentDoc) -> str:
    for s in doc.sections:
        if s.heading.strip().lower() == "overview":
            return s.body
    return ""


def apply_map(store: MazeStore, doc: ScentDoc) -> None:
    """Audit (fail-loud) then write a Map to the PROJECT layer under kind='map'.

    Mirrors ``trail.py::apply_trail``'s audited-write contract, generalized off
    Trail's git_sha stamping (a Map's sections are reference-pointer lists, not
    SQL-derived content, so there's no meaningful provenance to stamp beyond
    each section's existing ``source`` token).
    """
    from labrat.maze.scent_audit import ScentContaminationError, audit_scent_doc

    tag = audit_scent_doc(doc)
    if tag:
        raise ScentContaminationError(f"map {doc.domain!r} tripped contamination guard: {tag}")
    store.write_doc(doc, scope="project", kind="map")


class MapActivateScreen(ModalScreen[None]):
    """List Maps; toggle active/inactive (additive); create/curate/auto-seed."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss_screen", "Close", show=True),
        Binding("space", "toggle_active", "Toggle active", show=True),
        Binding("n", "new_map", "New Map", show=True),
        Binding("e", "edit_map", "Edit Map", show=True),
        Binding("s", "auto_seed", "Auto-seed from dbt", show=True),
    ]

    DEFAULT_CSS = """
    MapActivateScreen { align: center middle; }
    MapActivateScreen > Vertical {
        width: 80; height: 26;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    MapActivateScreen #maps-table { height: 1fr; }
    MapActivateScreen #actions { height: auto; margin-top: 1; }
    MapActivateScreen Button { margin: 0 1; min-width: 16; }
    MapActivateScreen #status { color: $text-muted; margin-top: 1; }
    """

    def __init__(
        self,
        store: MazeStore,
        active_maps: list[str],
        *,
        on_auto_seed: Callable[[], int] | None = None,
    ) -> None:
        super().__init__()
        self._store = store
        # SAME list object MainScreen handed to ToolContext — mutate in place
        # (append/remove) below; never reassign, or the live link breaks.
        self._active_maps = active_maps
        self._on_auto_seed = on_auto_seed
        self._maps: list[ScentDoc] = []

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("[bold]─ Maps · curate & activate ─[/bold]", id="title", markup=True)
            yield DataTable(id="maps-table", cursor_type="row")
            with Horizontal(id="actions"):
                yield Button("New  [N]", id="new-btn")
                yield Button("Edit  [E]", id="edit-btn")
                yield Button("Auto-seed from dbt  [S]", id="seed-btn")
                yield Button("Close  [Esc]", id="close-btn")
            yield Label("", id="status")

    def on_mount(self) -> None:
        table = self.query_one("#maps-table", DataTable)
        table.add_columns("Active?", "Map", "Scent members", "Trail members")
        self._reload()

    def _reload(self) -> None:
        from labrat.maze.map import scent_members, trail_members

        self._maps = sorted(self._store.docs(kind="map"), key=lambda d: d.domain)
        table = self.query_one("#maps-table", DataTable)
        table.clear()
        for doc in self._maps:
            state = _ACTIVE if doc.domain in self._active_maps else _INACTIVE
            table.add_row(
                state,
                doc.domain,
                str(len(scent_members(doc))),
                str(len(trail_members(doc))),
                key=doc.domain,
            )
        self._refresh_status()

    def _refresh_status(self) -> None:
        active = ", ".join(sorted(self._active_maps)) if self._active_maps else "none"
        self.query_one("#status", Label).update(f"Active: {active}")

    def _cursor_slug(self) -> str | None:
        table = self.query_one("#maps-table", DataTable)
        if table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key
        return None if key.value is None else str(key.value)

    def action_toggle_active(self) -> None:
        slug = self._cursor_slug()
        # Only ever offered from self._maps rows — a phantom slug can't reach here.
        if slug is None or slug not in {d.domain for d in self._maps}:
            return
        if slug in self._active_maps:
            self._active_maps.remove(slug)
            self.notify(f"Map deactivated: {slug}", timeout=3)
        else:
            self._active_maps.append(slug)
            self.notify(f"Map activated: {slug}", timeout=3)
        self._reload()

    @on(Button.Pressed, "#new-btn")
    def action_new_map(self) -> None:
        self._open_editor(None)

    @on(Button.Pressed, "#edit-btn")
    def action_edit_map(self) -> None:
        slug = self._cursor_slug()
        doc = next((d for d in self._maps if d.domain == slug), None)
        if doc is None:
            self.notify("Select a Map to edit (or press N for a new one).", severity="warning")
            return
        self._open_editor(doc)

    def _open_editor(self, doc: ScentDoc | None) -> None:
        def _after(saved_slug: str | None) -> None:
            if saved_slug:
                self.notify(f"\U0001f5fa Map saved: {saved_slug}", timeout=4)
            self._reload()

        self.app.push_screen(MapEditScreen(self._store, doc), _after)

    @on(Button.Pressed, "#seed-btn")
    def action_auto_seed(self) -> None:
        if self._on_auto_seed is None:
            self.notify("Auto-seed unavailable in this session.", severity="warning")
            return
        self._on_auto_seed()  # MainScreen already notifies the outcome
        self._reload()

    @on(Button.Pressed, "#close-btn")
    def action_dismiss_screen(self) -> None:
        self.dismiss(None)


class MapEditScreen(ModalScreen[str | None]):
    """Create/curate a Map: pick Scent + Trail members, edit Overview/Prompts.

    Mirrors ``TrailReviewScreen``'s modal shape (Static title, scrollable body,
    Button row, status Label) and ``HarvestReviewScreen``'s row-toggle
    convention for member selection (space toggles member/not-member per row).
    Dismisses with the saved Map's domain slug (None on cancel).
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Close", show=True),
        Binding("space", "toggle_row", "Include/exclude", show=True),
        Binding("ctrl+s", "save", "Save", show=True),
    ]

    DEFAULT_CSS = """
    MapEditScreen { align: center middle; }
    MapEditScreen > Vertical {
        width: 88; height: 36;
        border: thick $accent; background: $surface; padding: 1 2;
    }
    MapEditScreen #body { height: 1fr; }
    MapEditScreen .heading { margin-top: 1; text-style: bold; }
    MapEditScreen #slug-input { margin-bottom: 1; }
    MapEditScreen #overview { height: 5; }
    MapEditScreen #members-table { height: 10; }
    MapEditScreen #prompts { height: 5; }
    MapEditScreen #actions { height: auto; margin-top: 1; }
    MapEditScreen Button { margin: 0 1; min-width: 16; }
    MapEditScreen #status { color: $text-muted; }
    """

    def __init__(self, store: MazeStore, doc: ScentDoc | None = None) -> None:
        super().__init__()
        self._store = store
        self._doc = doc
        self._is_new = doc is None
        # Flat row model, mirrors HarvestReviewScreen: (kind, name); row key =
        # f"{kind}:{name}" (unique — a Scent domain and Trail domain sharing a
        # name can't collide on the DataTable's row key).
        self._rows: list[tuple[str, str]] = []
        self._included: dict[str, bool] = {}

    def compose(self) -> ComposeResult:
        from labrat.maze.map import map_prompts

        doc = self._doc
        title = f"Curate Map · {doc.domain}" if doc is not None else "New Map"
        overview = _overview_body(doc) if doc is not None else ""
        prompts = "\n".join(map_prompts(doc)) if doc is not None else ""

        with Vertical():
            yield Static(f"[bold]─ {title} ─[/bold]", id="title", markup=True)
            if self._is_new:
                yield Input(placeholder="Map slug, e.g. 'revenue'", id="slug-input")
            with VerticalScroll(id="body"):
                yield Label("Overview", classes="heading")
                yield TextArea(overview, id="overview")
                yield Label("Members (space toggles include/exclude)", classes="heading")
                yield DataTable(id="members-table", cursor_type="row")
                yield Label("Suggested Prompts (one per line)", classes="heading")
                yield TextArea(prompts, id="prompts")
            with Horizontal(id="actions"):
                yield Button("Save  [Ctrl+S]", id="save-btn", variant="primary")
                yield Button("Cancel  [Esc]", id="close-btn")
            yield Label("", id="status")

    def on_mount(self) -> None:
        from labrat.maze.map import scent_members, trail_members

        doc = self._doc
        current_scent = set(scent_members(doc)) if doc is not None else set()
        current_trails = set(trail_members(doc)) if doc is not None else set()

        scent_domains = sorted(d.domain for d in self._store.docs(kind="scent"))
        trail_domains = sorted(d.domain for d in self._store.docs(kind="trail"))
        self._rows = [("scent", d) for d in scent_domains] + [("trail", d) for d in trail_domains]
        self._included = {
            f"{kind}:{name}": (name in current_scent if kind == "scent" else name in current_trails)
            for kind, name in self._rows
        }

        table = self.query_one("#members-table", DataTable)
        table.add_columns("Member?", "Kind", "Domain")
        for kind, name in self._rows:
            row_key = f"{kind}:{name}"
            state = _INCLUDED if self._included[row_key] else _EXCLUDED
            table.add_row(state, kind, name, key=row_key)

    def action_toggle_row(self) -> None:
        table = self.query_one("#members-table", DataTable)
        if table.row_count == 0:
            return
        row = table.cursor_row
        key = table.coordinate_to_cell_key(Coordinate(row, 0)).row_key
        row_key = str(key.value)
        self._included[row_key] = not self._included[row_key]
        table.update_cell_at(
            Coordinate(row, 0), _INCLUDED if self._included[row_key] else _EXCLUDED
        )

    def _slug(self) -> str:
        if self._doc is not None:
            return self._doc.domain
        raw = self.query_one("#slug-input", Input).value
        return _slugify(raw)

    def _built_doc(self) -> ScentDoc:
        from labrat.maze.map import build_map_doc

        slug = self._slug()
        overview = self.query_one("#overview", TextArea).text
        prompts = _parse_bullet_lines(self.query_one("#prompts", TextArea).text)
        scent = sorted(
            name for kind, name in self._rows if kind == "scent" and self._included[f"scent:{name}"]
        )
        trails = sorted(
            name for kind, name in self._rows if kind == "trail" and self._included[f"trail:{name}"]
        )
        return build_map_doc(slug, scent=scent, trails=trails, prompts=prompts, overview=overview)

    @on(Button.Pressed, "#save-btn")
    def action_save(self) -> None:
        from labrat.maze.scent_audit import ScentContaminationError

        if self._is_new and not self.query_one("#slug-input", Input).value.strip():
            self.query_one("#status", Label).update("[red]Enter a Map slug first.[/red]")
            return
        doc = self._built_doc()
        try:
            apply_map(self._store, doc)
        except ScentContaminationError as exc:
            self.query_one("#status", Label).update(
                f"[red]Draft blocked by contamination audit: {exc}[/red]"
            )
            return
        except Exception as exc:  # never raise into the TUI
            self.query_one("#status", Label).update(f"[red]Failed to save Map: {exc}[/red]")
            return
        self.dismiss(doc.domain)

    @on(Button.Pressed, "#close-btn")
    def action_cancel(self) -> None:
        self.dismiss(None)
