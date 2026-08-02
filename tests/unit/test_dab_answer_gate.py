"""Deterministic answer gate for the DAB claude-mcp path.

Motivation (docs/dab-sonnet5-vs-luna-gap-analysis.md §f/§i/§k): prose levers have
repeatedly failed to fix delivery-shaped losses. `googlelocal:3` passed and failed on
nothing but `5-11PM` vs `5PM-11PM`; the convention-pinning lever never moved its target
across two runs and was dropped. A deterministic check cannot forget, so the checks that
can be expressed in code belong in code rather than in another prompt line.

Scope discipline: every check here must be defensible as GENERAL answer quality. None of
them may encode a value, label, threshold or cardinality taken from DAB ground truth, and
none may hard-code a grader window size reverse-engineered from `validate.py` — see the
`test_gate_is_untuned_*` tests at the bottom, which are the guard on that.
"""

from __future__ import annotations

from labrat.eval.benchmarks.dab.answer_gate import check_answer, requested_item_count

# --- requested_item_count -----------------------------------------------------------


def test_detects_explicit_top_n_request() -> None:
    assert requested_item_count("What are the top 5 businesses by rating?") == 5
    assert requested_item_count("List the 3 most common categories.") == 3


def test_detects_written_number_requests() -> None:
    assert requested_item_count("Give the top three products by revenue.") == 3


def test_returns_none_when_no_count_is_requested() -> None:
    assert requested_item_count("What is the average rating in Indianapolis?") is None


def test_does_not_mistake_incidental_numbers_for_a_count_request() -> None:
    """A year or a threshold is not a requested cardinality."""
    assert requested_item_count("What was the average rating in 2016?") is None
    assert requested_item_count("How many businesses have a rating above 4?") is None


# --- check_answer -------------------------------------------------------------------


def test_flags_an_empty_final_answer() -> None:
    v = check_answer(question="What is the average rating?", answer="")
    assert any(x.code == "empty_answer" for x in v)


def test_flags_fewer_items_than_requested() -> None:
    answer = "Analysis done.\n\n1. Alpha 4.9\n2. Beta 4.7"
    v = check_answer(question="What are the top 5 businesses by rating?", answer=answer)
    assert any(x.code == "count_shortfall" for x in v)
    assert "5" in next(x for x in v if x.code == "count_shortfall").detail


def test_accepts_an_answer_that_meets_the_requested_count() -> None:
    answer = "1. Alpha 4.9\n2. Beta 4.7\n3. Gamma 4.6\n4. Delta 4.5\n5. Eps 4.4"
    v = check_answer(question="What are the top 5 businesses by rating?", answer=answer)
    assert not any(x.code == "count_shortfall" for x in v)


def test_flags_a_markdown_table_for_a_name_value_list() -> None:
    """A table puts the value columns away from the name; our own trace analysis tied
    delivery-shape losses to exactly this. Generic readability, not a grader rule."""
    answer = "| Business | Rating |\n|---|---|\n| Alpha | 4.9 |\n| Beta | 4.7 |"
    v = check_answer(question="What are the top 2 businesses by rating?", answer=answer)
    assert any(x.code == "table_delivery" for x in v)


def test_does_not_flag_a_table_when_no_list_was_requested() -> None:
    answer = "| Metric | Value |\n|---|---|\n| Average | 3.55 |"
    v = check_answer(question="What is the average rating?", answer=answer)
    assert not any(x.code == "table_delivery" for x in v)


def test_flags_percentage_when_a_fraction_was_requested() -> None:
    v = check_answer(question="What fraction of repos use Python?", answer="About 28.6%")
    assert any(x.code == "fraction_as_percentage" for x in v)


def test_accepts_a_decimal_fraction() -> None:
    v = check_answer(question="What fraction of repos use Python?", answer="0.286")
    assert not any(x.code == "fraction_as_percentage" for x in v)


def test_a_clean_answer_produces_no_violations() -> None:
    answer = "1. Alpha 4.9\n2. Beta 4.7\n3. Gamma 4.6"
    assert check_answer(question="Top 3 businesses by rating?", answer=answer) == []


# --- untuned guards -----------------------------------------------------------------


def test_gate_is_untuned_no_dataset_names() -> None:
    """The gate must not encode DAB dataset knowledge — same rule as the prompt levers."""
    import inspect

    from labrat.eval.benchmarks.dab import answer_gate

    src = inspect.getsource(answer_gate).lower()
    for dataset in (
        "yelp",
        "googlelocal",
        "patents",
        "agnews",
        "music_brainz",
        "crmarenapro",
        "deps_dev",
        "github_repos",
        "pancancer",
        "stockindex",
        "stockmarket",
        "bookreview",
    ):
        assert dataset not in src


def test_gate_does_not_encode_a_reverse_engineered_grader_window() -> None:
    """A competitor derived K_ADJ=10 from a DAB validator's `llm_lower[idx:idx+10]`.
    That is scorer-fitting; we keep adjacency as a structural rule (no table) rather
    than a character budget copied out of the harness."""
    import inspect

    from labrat.eval.benchmarks.dab import answer_gate

    src = inspect.getsource(answer_gate)
    assert "llm_lower" not in src
    assert "validate.py" not in src


# --- row preservation on the table correction ---------------------------------------
#
# Measured harm, 2026-07-31 Opus run: of 22 table_delivery corrections, the two that
# cost trials both LOST ROWS. The model had produced a complete opening-hours table;
# the gate told it to restate as adjacent plain tokens; collapsing to prose dropped
# days ("Missing hours [Thursday, Closed]" / "[Saturday, 5-11PM]"). The verbatim value
# tokens survived intact -- it is row loss, not token corruption.


def test_table_correction_demands_every_row_be_kept() -> None:
    from labrat.eval.benchmarks.dab.answer_gate import Violation, format_violations

    msg = format_violations([Violation("table_delivery", "items are in a markdown table")])
    low = msg.lower()
    assert "every" in low and "row" in low, "must forbid dropping rows"
    assert "abbreviat" in low, "must forbid abbreviating labels when collapsing a table"
