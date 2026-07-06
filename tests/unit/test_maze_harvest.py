from __future__ import annotations

import pytest

from labrat.maze.harvest import cluster_corrections, draft_harvested_sections
from labrat.maze.scent_audit import ScentContaminationError
from labrat.memory.model import Memory, MemoryKind, MemoryScope


def _mem(text: str, table: str | None, kind: MemoryKind = MemoryKind.edit_derived) -> Memory:
    return Memory(profile="p", scope=MemoryScope.global_, kind=kind, text=text, table_scope=table)


def test_cluster_groups_by_table_scope() -> None:
    mems = [_mem("a", "orders"), _mem("b", "orders"), _mem("c", None)]
    clusters = cluster_corrections(mems)
    assert {m.text for m in clusters["orders"]} == {"a", "b"}
    assert clusters["__global__"][0].text == "c"


def test_cluster_ignores_non_correction_kinds() -> None:
    mems = [_mem("keep", "orders"), _mem("skip", "orders", kind=MemoryKind.explicit_user_rule)]
    clusters = cluster_corrections(mems)
    assert {m.text for m in clusters["orders"]} == {"keep"}


def test_draft_produces_harvested_gotchas_sections() -> None:
    clusters = cluster_corrections([_mem("Filter deleted_at IS NULL.", "orders")])
    sections = draft_harvested_sections(
        clusters, generated_at="2026-07-06T00:00:00Z", model_id="claude-sonnet-4-6"
    )
    assert len(sections) == 1
    s = sections[0]
    assert s.heading == "Gotchas"
    assert s.source == "harvested"
    assert s.generated_at == "2026-07-06T00:00:00Z"
    assert "- Filter deleted_at IS NULL." in s.body


def test_draft_fails_loud_on_contamination() -> None:
    # NOTE for implementer: read scent_audit.py's contamination patterns and craft a
    # text that trips detect_contamination (e.g. a reference to a ground-truth/answer-key
    # file). Confirm the exact smell token before finalizing this test.
    clusters = cluster_corrections([_mem("see ground_truth.csv for the answer", "orders")])
    with pytest.raises(ScentContaminationError):
        draft_harvested_sections(clusters, generated_at="2026-07-06T00:00:00Z")


def test_apply_approved_sections_writes_only_approved(tmp_path) -> None:
    from labrat.maze.document import Section
    from labrat.maze.harvest import apply_approved_sections
    from labrat.maze.store import MazeStore

    store = MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="default")
    approved = [Section(heading="Gotchas", body="- Keep this.", source="harvested")]
    apply_approved_sections(store, domain="sales", approved=approved)
    doc = store.load_domain("sales")
    assert doc is not None
    assert any("Keep this." in s.body and s.source == "harvested" for s in doc.sections)


def test_apply_approved_sections_preserves_prior_sections(tmp_path) -> None:
    from labrat.maze.document import Section
    from labrat.maze.harvest import apply_approved_sections
    from labrat.maze.store import MazeStore

    store = MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="default")
    first = [Section(heading="Gotchas", body="- First correction.", source="harvested")]
    apply_approved_sections(store, domain="sales", approved=first)

    second = [Section(heading="Gotchas", body="- Second correction.", source="harvested")]
    apply_approved_sections(store, domain="sales", approved=second)

    doc = store.load_domain("sales")
    assert doc is not None
    bodies = {s.body for s in doc.sections}
    assert "- First correction." in bodies
    assert "- Second correction." in bodies


def test_apply_approved_sections_is_idempotent(tmp_path) -> None:
    from labrat.maze.document import Section
    from labrat.maze.harvest import apply_approved_sections
    from labrat.maze.store import MazeStore

    store = MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="default")
    section = Section(heading="Gotchas", body="- Same correction.", source="harvested")
    apply_approved_sections(store, domain="sales", approved=[section])
    apply_approved_sections(store, domain="sales", approved=[section])

    doc = store.load_domain("sales")
    assert doc is not None
    matches = [s for s in doc.sections if s.body.strip() == "- Same correction."]
    assert len(matches) == 1


def test_apply_approved_sections_audits_before_write(tmp_path) -> None:
    from labrat.maze.document import Section
    from labrat.maze.harvest import apply_approved_sections
    from labrat.maze.store import MazeStore

    store = MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="default")
    tainted = [Section(heading="Gotchas", body="- refers to ground_truth.csv", source="harvested")]
    with pytest.raises(ScentContaminationError):
        apply_approved_sections(store, domain="sales", approved=tainted)
    assert store.load_domain("sales") is None


def test_draft_fails_loud_on_mixed_clean_and_contaminated() -> None:
    clusters = cluster_corrections(
        [
            _mem("Filter deleted_at IS NULL.", "orders"),
            _mem("see ground_truth.csv for the answer", "orders"),
        ]
    )
    with pytest.raises(ScentContaminationError):
        draft_harvested_sections(clusters, generated_at="2026-07-06T00:00:00Z")
