"""Trail v1 draft + promote (maze/trail.py)."""

from datetime import UTC, datetime

import pytest

from labrat.cheese.model import FindingProvenance
from labrat.maze.scent_audit import ScentContaminationError
from labrat.maze.store import MazeStore
from labrat.maze.trail import (
    applicable_validations,
    apply_trail,
    draft_trail_from_finding,
    intent_slug,
    referenced_tables,
)
from labrat.thread.model import Finding
from labrat.validations.model import ValidationRule


def _finding(
    sql: str, *, verified: bool, question: str = "How do we compute monthly retention?"
) -> Finding:
    prov = None
    if verified:
        prov = FindingProvenance(
            scent_sources=[],
            joins_verified=1,
            lineage_used=False,
            verifier_verdict="sufficient (1 round)",
            run_sql_count=2,
            schema_fingerprint=None,
            git_sha=None,
            model_id=None,
            captured_at=datetime.now(tz=UTC),
        )
    return Finding(
        id="f1",
        version_id="v1",
        question=question,
        sql=sql,
        results_ref=None,
        chart_spec=None,
        note="Exclude test accounts.",
        pinned_at=datetime.now(tz=UTC),
        provenance=prov,
    )


def test_intent_slug():
    assert (
        intent_slug("How do we compute Monthly Retention?") == "how-do-we-compute-monthly-retention"
    )


def test_referenced_tables():
    tables = referenced_tables("SELECT * FROM events e JOIN users u ON e.uid = u.id")
    assert set(tables) == {"events", "users"}


def test_referenced_tables_excludes_cte_alias():
    tables = referenced_tables("WITH cte AS (SELECT * FROM events) SELECT * FROM cte")
    assert tables == ["events"]


def test_applicable_validations_table_and_global():
    rules = [
        ValidationRule(
            profile="p",
            natural_language_rule="events must filter is_test",
            table_scope="events",
            enabled=True,
        ),
        ValidationRule(
            profile="p",
            natural_language_rule="orders need status",
            table_scope="orders",
            enabled=True,
        ),
        ValidationRule(
            profile="p", natural_language_rule="global rule", table_scope=None, enabled=True
        ),
        ValidationRule(
            profile="p", natural_language_rule="disabled", table_scope="events", enabled=False
        ),
    ]
    got = {r.natural_language_rule for r in applicable_validations(rules, ["events"])}
    assert got == {"events must filter is_test", "global rule"}


def test_draft_verified_vs_draft_source():
    d_ver = draft_trail_from_finding(
        _finding("SELECT 1 FROM events", verified=True),
        all_validations=[],
        generated_at="2026-07-10T00:00:00Z",
    )
    d_unv = draft_trail_from_finding(
        _finding("SELECT 1 FROM events", verified=False),
        all_validations=[],
        generated_at="2026-07-10T00:00:00Z",
    )
    assert {s.source for s in d_ver.sections} == {"verified"}
    assert {s.source for s in d_unv.sections} == {"draft"}


def test_draft_structure():
    doc = draft_trail_from_finding(
        _finding("SELECT uid FROM events", verified=True),
        all_validations=[
            ValidationRule(
                profile="p",
                natural_language_rule="events must filter is_test",
                table_scope="events",
                enabled=True,
            )
        ],
        generated_at="2026-07-10T00:00:00Z",
        schema_hash="fp1",
    )
    assert doc.kind == "trail"
    assert doc.domain == "how-do-we-compute-monthly-retention"
    assert doc.tables == ["events"]
    headings = [s.heading for s in doc.sections]
    assert headings == ["When to use", "Steps", "Reference SQL", "Validations", "Gotchas"]
    body = {s.heading: s.body for s in doc.sections}
    assert "SELECT uid FROM events" in body["Reference SQL"]
    assert "events must filter is_test" in body["Validations"]
    assert "Exclude test accounts." in body["Gotchas"]
    assert all(s.schema_hash == "fp1" for s in doc.sections)


def test_draft_contamination_fails_loud():
    # A reference SQL that reads a ground-truth/answer artifact must trip the audit.
    bad = _finding("SELECT * FROM ground_truth", verified=False)
    with pytest.raises(ScentContaminationError):
        draft_trail_from_finding(bad, all_validations=[], generated_at="2026-07-10T00:00:00Z")


def test_apply_trail_writes_kind_trail(tmp_path):
    store = MazeStore(project_root=tmp_path, home=tmp_path / "home", profile="default")
    doc = draft_trail_from_finding(
        _finding("SELECT 1 FROM events", verified=True),
        all_validations=[],
        generated_at="2026-07-10T00:00:00Z",
    )
    apply_trail(store, doc)
    reread = store.docs(kind="trail")
    assert len(reread) == 1 and reread[0].kind == "trail"
    assert reread[0].domain == "how-do-we-compute-monthly-retention"
    # written to the project layer's trail/ dir
    assert (tmp_path / "labrat_maze" / "trail" / "how-do-we-compute-monthly-retention.md").exists()
