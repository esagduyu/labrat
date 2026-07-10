"""TrailReviewScreen: widget-id guard + overwrite-warning rendering (isolated, no DB fixture)."""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.widgets import Label, Static

from labrat.maze.document import ScentDoc, Section
from labrat.maze.store import MazeStore
from labrat.screens.trail_review import TrailReviewScreen


class _Host(App[None]):
    def __init__(self, screen: TrailReviewScreen) -> None:
        super().__init__()
        self._screen = screen

    def compose(self) -> ComposeResult:
        yield Static("")

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def _doc(*, heading: str = "Weird Heading!!") -> ScentDoc:
    return ScentDoc(
        domain="d1",
        kind="trail",
        tables=[],
        sections=[Section(heading=heading, body="body text", source="draft")],
    )


async def test_compose_survives_noncanonical_heading(tmp_path: Path) -> None:
    # A heading outside _FIELD_IDS used to fall back to a raw `.lower()` id
    # (spaces/punctuation intact) -> invalid Textual widget id -> compose crash.
    store = MazeStore(project_root=tmp_path, home=tmp_path / "home", profile="default")
    screen = TrailReviewScreen(_doc(), store)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        # Sanitized id: "Weird Heading!!" -> "weird-heading"
        widget = pilot.app.screen.query_one("#field-weird-heading", Static)
        assert str(widget.render()) == "body text"


async def test_overwrite_warning_renders_when_true(tmp_path: Path) -> None:
    store = MazeStore(project_root=tmp_path, home=tmp_path / "home", profile="default")
    screen = TrailReviewScreen(_doc(), store, overwrites=True)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        label = pilot.app.screen.query_one("#overwrite-warning", Label)
        rendered = str(label.render())
        assert "overwrites existing Trail" in rendered
        assert "d1" in rendered


async def test_overwrite_warning_absent_when_false(tmp_path: Path) -> None:
    store = MazeStore(project_root=tmp_path, home=tmp_path / "home", profile="default")
    screen = TrailReviewScreen(_doc(), store, overwrites=False)
    async with _Host(screen).run_test() as pilot:
        await pilot.pause()
        try:
            pilot.app.screen.query_one("#overwrite-warning", Label)
        except NoMatches:
            pass
        else:
            raise AssertionError("overwrite warning should not render when overwrites=False")
