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


def test_draft_produces_domain_keyed_harvested_sections() -> None:
    clusters = cluster_corrections(
        [_mem("filter test orders", "orders"), _mem("dates are UTC", None)]
    )
    drafts = draft_harvested_sections(clusters, generated_at="2026-07-06")
    assert set(drafts) == {"orders", "__global__"}
    for sections in drafts.values():
        for s in sections:
            assert s.heading == "Gotchas"
            assert s.source == "harvested"
            assert s.generated_at == "2026-07-06"
    assert "filter test orders" in drafts["orders"][0].body


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


def test_apply_never_copies_user_layer_content(tmp_path) -> None:
    # Non-negotiable #2: user-layer (Cartographer) sections must not be written project-side.
    from labrat.maze.document import ScentDoc, Section, render_document
    from labrat.maze.harvest import apply_approved_sections
    from labrat.maze.store import MazeStore

    store = MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="p1")
    user_dir = tmp_path / "home" / ".labrat" / "maze" / "p1" / "scent"
    user_dir.mkdir(parents=True)
    cart = ScentDoc(
        domain="orders",
        sections=[Section(heading="Key Tables", body="- orders: 8 rows", source="verified")],
    )
    (user_dir / "orders.md").write_text(render_document(cart), encoding="utf-8")

    apply_approved_sections(
        store,
        "orders",
        [Section(heading="Gotchas", body="- exclude test orders", source="harvested")],
    )

    project_doc = store.load_domain("orders", scope="project")
    assert project_doc is not None
    assert [s.heading for s in project_doc.sections] == ["Gotchas"]  # NO Key Tables copy
    merged = store.load_domain("orders")
    assert merged is not None
    assert {s.heading for s in merged.sections} == {"Key Tables", "Gotchas"}


def test_apply_idempotent_against_project_layer(tmp_path) -> None:
    from labrat.maze.document import Section
    from labrat.maze.harvest import apply_approved_sections
    from labrat.maze.store import MazeStore

    store = MazeStore(project_root=tmp_path / "proj", home=tmp_path / "home", profile="p1")
    approved = [Section(heading="Gotchas", body="- dates are UTC", source="harvested")]
    apply_approved_sections(store, "general", approved)
    apply_approved_sections(store, "general", approved)  # re-approve
    doc = store.load_domain("general", scope="project")
    assert doc is not None and len(doc.sections) == 1


def _decision(text: str, table_scope: str | None = None) -> Memory:
    return Memory(
        profile="p",
        scope=MemoryScope.global_,
        kind=MemoryKind.explicit_user_rule,
        text=text,
        table_scope=table_scope,
    )


def test_cluster_decisions_only_explicit_rules() -> None:
    from labrat.maze.harvest import cluster_decisions

    corr = _mem("c", None, kind=MemoryKind.chat_correction)
    clusters = cluster_decisions([_decision("attribute revenue at order time", "orders"), corr])
    assert set(clusters) == {"orders"}  # the correction is excluded


def test_draft_decision_sections_heading_and_source() -> None:
    from labrat.maze.harvest import cluster_decisions, draft_decision_sections

    drafts = draft_decision_sections(
        cluster_decisions([_decision("exclude is_test from metrics", "events")]),
        generated_at="2026-07-10T00:00:00Z",
    )
    sec = drafts["events"][0]
    assert sec.heading == "Decisions" and sec.source == "harvested"
    assert "exclude is_test from metrics" in sec.body


def test_draft_decision_contamination_fails_loud() -> None:
    from labrat.maze.harvest import cluster_decisions, draft_decision_sections

    with pytest.raises(ScentContaminationError):
        draft_decision_sections(
            cluster_decisions([_decision("see ground_truth.csv", "t")]),
            generated_at="2026-07-10T00:00:00Z",
        )
