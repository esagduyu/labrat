"""Data-analysis SOP state + render (FEATURE_ROADMAP #30)."""

from __future__ import annotations

import pytest

from labrat.agent.workflow import DATA_ANALYSIS_WORKFLOW, STEP_KEYS, WorkflowState


def test_canonical_workflow_has_nine_ordered_steps() -> None:
    keys = [s.key for s in DATA_ANALYSIS_WORKFLOW]
    assert keys == [
        "clarify",
        "consult_scent",
        "ground",
        "plan",
        "query",
        "repair",
        "verify_joins",
        "verify_answer",
        "review",
    ]
    assert STEP_KEYS == tuple(keys)


def test_new_state_all_pending() -> None:
    st = WorkflowState.new()
    assert set(st.statuses) == set(STEP_KEYS)
    assert all(v == "pending" for v in st.statuses.values())
    assert st.repair_attempts == 0


def test_mark_transitions_and_render_is_ordered() -> None:
    st = WorkflowState.new()
    st.mark("clarify", "done")
    st.mark("query", "doing", note="running step 1")
    r = st.render()
    assert r.index("clarify") < r.index("query") < r.index("verify_answer")
    assert "[x] clarify" in r
    assert "[~] query" in r
    assert "running step 1" in r


def test_mark_unknown_step_raises() -> None:
    st = WorkflowState.new()
    with pytest.raises(ValueError):
        st.mark("nonsense", "done")


def test_repair_flag_appears_at_cap() -> None:
    st = WorkflowState.new()
    for _ in range(3):
        st.note_repair_failure()
    assert st.repair_attempts == 3
    assert "failed attempts" in st.render()


def test_repair_no_flag_below_cap() -> None:
    st = WorkflowState.new()
    st.note_repair_failure()
    assert "failed attempts" not in st.render()
