"""HarvestReviewScreen: approve → apply (audited); cancel → nothing written."""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Static

from labrat.maze.document import Section
from labrat.maze.store import MazeStore
from labrat.screens.harvest_review import HarvestReviewScreen


class _Host(App[None]):
    def __init__(self, screen: HarvestReviewScreen) -> None:
        super().__init__()
        self._screen = screen
        self.result: int | None = None

    def compose(self) -> ComposeResult:
        yield Static("")

    def on_mount(self) -> None:
        def _cb(result: int | None) -> None:
            self.result = result

        self.push_screen(self._screen, _cb)


def _drafts() -> dict[str, list[Section]]:
    return {
        "orders": [
            Section(
                heading="Gotchas",
                body="- filter test orders",
                source="harvested",
                generated_at="2026-07-06",
            )
        ],
        "__global__": [
            Section(
                heading="Gotchas",
                body="- dates are UTC",
                source="harvested",
                generated_at="2026-07-06",
            )
        ],
    }


def _store(tmp_path: Path) -> MazeStore:
    return MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="p1")


async def test_apply_all_approved_writes_domains(tmp_path: Path) -> None:
    store = _store(tmp_path)
    host = _Host(HarvestReviewScreen(_drafts(), store))
    async with host.run_test() as pilot:
        await pilot.pause()
        # Rows default to approved; apply everything.
        await pilot.click("#apply-btn")
        await pilot.pause()
    assert host.result == 2
    orders = store.load_domain("orders")
    assert orders is not None and "filter test orders" in orders.sections[-1].body
    general = store.load_domain("general")  # __global__ routed via domain_for_cluster
    assert general is not None and "dates are UTC" in general.sections[-1].body


async def test_cancel_writes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    host = _Host(HarvestReviewScreen(_drafts(), store))
    async with host.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert host.result == 0
    assert store.load_domain("orders") is None
    assert store.load_domain("general") is None


async def test_contaminated_draft_fails_loud_and_writes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    bad = {
        "orders": [
            Section(
                heading="Gotchas",
                body="- see ground_truth.csv for the answer",
                source="harvested",
                generated_at="2026-07-06",
            )
        ]
    }
    host = _Host(HarvestReviewScreen(bad, store))
    async with host.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#apply-btn")
        await pilot.pause()
        # Screen stays open showing the audit error; nothing written.
        assert "contamin" in str(
            pilot.app.screen.query_one("#status").render()
        ).lower() or "answer_key" in str(pilot.app.screen.query_one("#status").render())
    assert store.load_domain("orders") is None
