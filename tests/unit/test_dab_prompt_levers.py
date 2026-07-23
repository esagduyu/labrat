"""Benchmark-safe process levers in the DAB driver prompts (FEATURE: DAB prompt levers)."""

from __future__ import annotations

from labrat.agent.tools.base import ToolContext
from labrat.eval.benchmarks.dab.env import DabTaskEnv
from labrat.eval.benchmarks.dab.suite import (
    _build_labrat_agent_system_prompt,
    _dab_lever_lines,
)


def test_lever_lines_cover_the_three_levers() -> None:
    text = " ".join(_dab_lever_lines())
    assert "never answer from prior" in text  # force-query
    assert "error_category" in text and "hint" in text  # repair via run_sql diagnostics
    assert "GROUP BY" in text  # push aggregation into SQL


def test_labrat_agent_prompt_includes_every_lever() -> None:
    env = DabTaskEnv(
        ctx=ToolContext(connections={}, catalogs={}, primary="x"), attachable=[], mongo=[]
    )
    prompt = _build_labrat_agent_system_prompt(env)
    for lever in _dab_lever_lines():
        assert lever in prompt


def test_labrat_agent_prompt_can_disable_every_lever() -> None:
    env = DabTaskEnv(
        ctx=ToolContext(connections={}, catalogs={}, primary="x"), attachable=[], mongo=[]
    )
    prompt = _build_labrat_agent_system_prompt(env, include_levers=False)
    for lever in _dab_lever_lines():
        assert lever not in prompt


def test_top_n_with_ties_lever_present() -> None:
    assert any("truncates ties" in ln for ln in _dab_lever_lines())


def test_lever_lines_now_number_seven() -> None:
    assert len(_dab_lever_lines()) == 7


def test_enumeration_completeness_lever_present() -> None:
    text = " ".join(_dab_lever_lines())
    assert "every qualifying item" in text
    assert "distinct qualifying groups" in text


def test_tie_band_sanity_lever_present() -> None:
    text = " ".join(_dab_lever_lines())
    assert "tie band" in text
    assert "tied roster" in text


def test_adjacent_token_delivery_lever_present() -> None:
    text = " ".join(_dab_lever_lines())
    assert "directly adjacent as plain tokens" in text
    assert "markdown table" in text


def test_lever_lines_are_untuned_no_dataset_names_or_answer_content() -> None:
    """Levers stay 'untuned prompt' under DAB's definition — pure process/structure,
    no dataset names, no answer content."""
    text = " ".join(_dab_lever_lines()).lower()
    for forbidden in (
        "yelp",
        "googlelocal",
        "stockmarket",
        "stockindex",
        "bookreview",
        "crmarena",
        "crmarenapro",
        "deps_dev",
        "deps_dev_v1",
        "agnews",
        "music_brainz",
        "github_repos",
        "patents",
        "pancancer",
        "ground truth",
        "gold",
        "dab",
        "benchmark",
        "validator",
        "leaderboard",
    ):
        assert forbidden not in text, forbidden


def _env() -> DabTaskEnv:
    return DabTaskEnv(
        ctx=ToolContext(connections={}, catalogs={}, primary="x"), attachable=[], mongo=[]
    )


def test_taxonomy_off_by_default_and_prompt_byte_identical() -> None:
    from labrat.eval.benchmarks.dab.suite import _taxonomy_lines

    base = _build_labrat_agent_system_prompt(_env())
    explicit_off = _build_labrat_agent_system_prompt(_env(), include_taxonomy=False)
    assert base == explicit_off
    for line in _taxonomy_lines():
        assert line not in base


def test_taxonomy_on_appends_answer_discipline_section() -> None:
    from labrat.eval.benchmarks.dab.suite import _taxonomy_lines

    prompt = _build_labrat_agent_system_prompt(_env(), include_taxonomy=True)
    assert "Answer discipline:" in prompt
    for line in _taxonomy_lines():
        assert line in prompt


def test_taxonomy_text_is_benchmark_agnostic() -> None:
    from labrat.eval.benchmarks.dab.suite import _taxonomy_lines

    text = " ".join(_taxonomy_lines()).lower()
    for forbidden in (
        "dab",
        "benchmark",
        "validator",
        "leaderboard",
        "agnews",
        "yelp",
        "stockindex",
        "stockmarket",
        "patents",
        "crmarena",
        "bookreview",
        "googlelocal",
        "music_brainz",
        "deps_dev",
        "github_repos",
        "pancancer",
        "ground truth",
        "gold",
    ):
        assert forbidden not in text, forbidden


def test_taxonomy_covers_the_seven_disciplines() -> None:
    from labrat.eval.benchmarks.dab.suite import _taxonomy_lines

    text = " ".join(_taxonomy_lines())
    assert "one row per" in text  # shape
    assert "native" in text and "code" in text  # grain
    assert "every qualifying item" in text  # delivery / enumeration
    assert "decimal" in text  # delivery / numeric form
    assert "inclusive" in text  # literal reading
    assert "Re-run" in text  # verify before commit
    assert "most defensible" in text  # interpretation enumeration
    assert "does not contain" in text  # legitimate not-supported answer
    assert "deterministic rule" in text  # bulk categorization
    assert "exactly as" in text and "stored" in text  # verbatim entity names (D1)
