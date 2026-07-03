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


def test_city_state_answer_not_flagged() -> None:
    # "Chicago, IL" is one answer, not 2 items — top-3 question must not flag
    assert check_answer_constraints("top 3 cities", "Chicago, IL") == []


def test_prose_enumeration_with_and_satisfies_count() -> None:
    # "A, B and C" is 3 items → top-3 satisfied, no flag
    assert check_answer_constraints("top 3 products", "A, B and C") == []


def test_five_item_and_list_satisfies() -> None:
    assert check_answer_constraints("top 5 products", "A, B, C, D and E") == []


def test_proportion_bare_decimal_not_flagged() -> None:
    assert check_answer_constraints("What proportion of users churned?", "0.42") == []


def test_preamble_line_plus_list_line_not_flagged() -> None:
    # FIX 2: a preamble line + a comma-separated list line on the next line must count
    # as 5 items (the list-line split), not 2 lines — was a false positive before.
    v = check_answer_constraints(
        "What are the top 5 products?",
        "The top 5 products are:\nWidget, Gadget, Gizmo, Doohickey, Thingamajig",
    )
    assert v == []


def test_preamble_line_plus_three_item_list_line_satisfies_top_3() -> None:
    v = check_answer_constraints("top 3 products", "Here are the top 3:\nA, B, C")
    assert v == []
