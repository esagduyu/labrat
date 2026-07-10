from __future__ import annotations

from pathlib import Path


def _decision(text: str, table_scope: str | None = None):
    from labrat.memory.model import Memory, MemoryKind, MemoryScope

    return Memory(
        profile="p",
        scope=MemoryScope.global_,
        kind=MemoryKind.explicit_user_rule,
        text=text,
        table_scope=table_scope,
    )


def test_cluster_decisions_only_explicit_rules() -> None:
    from labrat.maze.harvest import cluster_decisions
    from labrat.memory.model import Memory, MemoryKind, MemoryScope

    corr = Memory(profile="p", scope=MemoryScope.global_, kind=MemoryKind.chat_correction, text="c")
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
    import pytest

    from labrat.maze.harvest import cluster_decisions, draft_decision_sections
    from labrat.maze.scent_audit import ScentContaminationError

    with pytest.raises(ScentContaminationError):
        draft_decision_sections(
            cluster_decisions([_decision("see ground_truth.csv", "t")]),
            generated_at="2026-07-10T00:00:00Z",
        )


def test_filter_unpromoted_drops_already_promoted(tmp_path: Path) -> None:
    from labrat.maze.harvest import (
        apply_approved_sections,
        cluster_decisions,
        draft_decision_sections,
    )
    from labrat.maze.store import MazeStore
    from labrat.screens.harvest_controller import filter_unpromoted_decisions

    store = MazeStore(project_root=tmp_path, home=tmp_path / "h", profile="default")
    d = _decision("attribute revenue at order time", "orders")
    # promote it once
    drafts = draft_decision_sections(cluster_decisions([d]), generated_at="2026-07-10T00:00:00Z")
    apply_approved_sections(store, "orders", drafts["orders"])
    # now it's promoted → filtered out; a NEW decision survives
    d2 = _decision("new rule about refunds", "orders")
    survivors = filter_unpromoted_decisions([d, d2], store)
    assert [m.text for m in survivors] == ["new rule about refunds"]


def test_merge_drafts_concats_per_domain() -> None:
    from labrat.maze.document import Section
    from labrat.screens.harvest_controller import merge_drafts

    a = {"orders": [Section(heading="Gotchas", body="g")]}
    b = {
        "orders": [Section(heading="Decisions", body="d")],
        "events": [Section(heading="Decisions", body="e")],
    }
    merged = merge_drafts(a, b)
    assert [s.heading for s in merged["orders"]] == ["Gotchas", "Decisions"]
    assert "events" in merged


def test_review_decisions_end_to_end(tmp_path: Path) -> None:
    from labrat.maze.store import MazeStore
    from labrat.screens.harvest_controller import review_decisions

    store = MazeStore(project_root=tmp_path, home=tmp_path / "h", profile="default")
    d = _decision("attribute revenue at order time", "orders")
    drafts = review_decisions([d], store, generated_at="2026-07-10T00:00:00Z")
    assert set(drafts) == {"orders"}
    assert drafts["orders"][0].heading == "Decisions"
    assert "attribute revenue at order time" in drafts["orders"][0].body
