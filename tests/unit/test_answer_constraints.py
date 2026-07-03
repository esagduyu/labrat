from __future__ import annotations

from labrat.agent.verification.constraints import check_answer_constraints


def test_top_n_count_mismatch_flagged() -> None:
    v = check_answer_constraints("What are the top 5 products by revenue?", "Widget, Gadget, Gizmo")
    assert any("5" in s for s in v)  # asked for 5, answer lists 3


def test_top_n_satisfied_no_flag() -> None:
    v = check_answer_constraints("top 3 products", "A\nB\nC")
    assert v == []


def test_percentage_expected_but_absent_flagged() -> None:
    v = check_answer_constraints("What percentage of users churned?", "About 4 thousand users")
    assert any("percent" in s.lower() for s in v)


def test_percentage_present_no_flag() -> None:
    assert check_answer_constraints("what percentage churned?", "12.5%") == []


def test_no_extractable_constraint_no_flag() -> None:
    assert check_answer_constraints("Which city has the most stores?", "Chicago") == []
