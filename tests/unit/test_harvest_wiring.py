from __future__ import annotations

from pathlib import Path

from labrat.maze.harvest import apply_approved_sections
from labrat.maze.store import MazeStore
from labrat.memory.model import Memory, MemoryKind, MemoryScope


def test_review_then_apply_writes_only_approved(tmp_path: Path) -> None:
    from labrat.screens.harvest_controller import review_corrections

    mems = [
        Memory(
            profile="p",
            scope=MemoryScope.global_,
            kind=MemoryKind.edit_derived,
            text="Filter deleted_at IS NULL.",
            table_scope="sales",
        )
    ]
    drafted = review_corrections(mems, generated_at="2026-07-06T00:00:00Z")
    assert drafted and drafted[0].source == "harvested"

    # Simulate a human approving the first bullet only.
    approved = [drafted[0]]
    store = MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="default")
    apply_approved_sections(store, domain="sales", approved=approved)
    doc = store.load_domain("sales")
    assert doc is not None
    assert any("deleted_at IS NULL" in s.body for s in doc.sections)


def test_harvesting_enabled_requires_interactive_and_opt_in() -> None:
    from labrat.screens.harvest_controller import harvesting_enabled

    assert harvesting_enabled(is_interactive=True, profile_opt_in=True) is True
    assert harvesting_enabled(is_interactive=False, profile_opt_in=True) is False
    assert harvesting_enabled(is_interactive=True, profile_opt_in=False) is False
    assert harvesting_enabled(is_interactive=False, profile_opt_in=False) is False
