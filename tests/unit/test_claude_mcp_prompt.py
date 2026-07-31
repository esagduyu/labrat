"""The claude-mcp opening-prompt builder (extracted for the leaderboard prompt audit)."""

from __future__ import annotations

from pathlib import Path

from labrat.agent.tools.base import ToolContext
from labrat.db.catalog import Catalog
from labrat.eval.benchmarks.dab.env import AttachSpec, DabTaskEnv
from labrat.eval.benchmarks.dab.suite import _build_claude_mcp_prompt
from labrat.eval.types import BenchmarkTask


def _env(attachable: list[AttachSpec] | None = None) -> DabTaskEnv:
    ctx = ToolContext(
        connections={"main": object()},
        catalogs={"main": Catalog(database_name="main", schemas=[])},
        primary="main",
    )
    return DabTaskEnv(ctx=ctx, attachable=attachable or [], mongo=[])


def _task() -> BenchmarkTask:
    return BenchmarkTask(id="demo:1", benchmark="dab", prompt="DESCRIPTION...\n\nHow many rows?")


def test_prompt_contains_question_and_levers() -> None:
    p = _build_claude_mcp_prompt(
        "main", _env(), _task(), include_cartographer_line=False, max_tool_calls=None
    )
    assert "How many rows?" in p  # the task (description + question) is embedded
    assert "never answer from prior" in p  # a process lever
    assert "restate the final answer on the last line" in p
    assert "search_reference_docs" not in p  # cartographer line absent when off


def test_cartographer_line_included_when_on() -> None:
    p = _build_claude_mcp_prompt(
        "main", _env(), _task(), include_cartographer_line=True, max_tool_calls=None
    )
    assert "search_reference_docs" in p  # cartographer consult line present


def test_prompt_levers_can_be_disabled() -> None:
    p = _build_claude_mcp_prompt(
        "main",
        _env(),
        _task(),
        include_cartographer_line=False,
        max_tool_calls=None,
        include_levers=False,
    )
    assert "How many rows?" in p
    assert "never answer from prior" not in p


def test_prompt_lists_secondary_duckdb(tmp_path: Path) -> None:
    """Task 4 widens AttachSpec.db_type to include a secondary DuckDB file; the
    claude-mcp prompt must surface it under the attachable-databases section
    (alias / path / db_type) exactly like the existing sqlite/postgres specs do."""
    spec = AttachSpec(
        alias="secondary",
        path=str(tmp_path / "secondary.duckdb"),
        db_type="duckdb",
    )
    p = _build_claude_mcp_prompt(
        "main",
        _env(attachable=[spec]),
        _task(),
        include_cartographer_line=False,
        max_tool_calls=None,
    )
    assert "Secondary databases you can bring in via attach_database" in p
    assert f"secondary / {tmp_path / 'secondary.duckdb'} / duckdb" in p


def test_tool_guidance_off_by_default() -> None:
    p = _build_claude_mcp_prompt(
        "main", _env(), _task(), include_cartographer_line=False, max_tool_calls=None
    )
    for tool in ("profile_dataset", "workflow", "column_stats", "check_sql"):
        assert tool not in p


def test_tool_guidance_names_the_zero_call_tools_and_reconciles_cartographer() -> None:
    p = _build_claude_mcp_prompt(
        "main",
        _env(),
        _task(),
        include_cartographer_line=True,
        max_tool_calls=None,
        include_tool_guidance=True,
    )
    for tool in ("profile_dataset", "workflow", "column_stats", "check_sql"):
        assert tool in p
    assert "before profiling" not in p
    assert p.index("search_reference_docs") < p.index("profile_dataset")


def test_informed_packs_are_off_by_default_and_composable() -> None:
    off = _build_claude_mcp_prompt(
        "main", _env(), _task(), include_cartographer_line=False, max_tool_calls=None
    )
    assert "coded form" not in off
    assert "very first sentence" not in off

    on = _build_claude_mcp_prompt(
        "main",
        _env(),
        _task(),
        include_cartographer_line=False,
        max_tool_calls=None,
        informed_shape=True,
        informed_validator=True,
    )
    assert "coded form" in on
    assert "very first sentence" in on


def test_informed_packs_off_path_is_byte_identical_to_no_new_kwargs() -> None:
    """The four informed-pack flags must not move the prompt at all when unset — a
    completed 270-trial benchmark run is only comparable if the unflagged path is
    untouched. Compare against a call that doesn't even pass the new kwargs."""
    with_flags_off = _build_claude_mcp_prompt(
        "main",
        _env(),
        _task(),
        include_cartographer_line=False,
        max_tool_calls=None,
        informed_shape=False,
        informed_validator=False,
        informed_conventions=False,
        informed_datasets=False,
    )
    without_new_kwargs = _build_claude_mcp_prompt(
        "main", _env(), _task(), include_cartographer_line=False, max_tool_calls=None
    )
    assert with_flags_off == without_new_kwargs


def test_claude_mcp_off_path_matches_the_frozen_pre_packs_baseline() -> None:
    """Anchors the unflagged prompt to its state before the informed packs landed.

    Captured from commit f6b9461 (the commit immediately before the packs were wired)
    by building this exact prompt in a temporary worktree. A completed 270-trial
    benchmark run is only comparable to future runs if this path never drifts, so any
    edit that changes the default prompt must fail here and be a deliberate decision.

    If this test fails, do NOT update the hash to make it pass unless you intend to
    invalidate comparisons against that baseline run.
    """
    import hashlib

    prompt = _build_claude_mcp_prompt(
        "main",
        _env(),
        BenchmarkTask(id="pancancer_atlas:1", benchmark="dab", prompt="Q"),
        include_cartographer_line=True,
        max_tool_calls=None,
    )
    assert (
        hashlib.sha256(prompt.encode()).hexdigest()
        == "a59349f11ed7213781c503c7668a2d5b80d8ce20f88a31d53501396fdf203688"
    )


def test_informed_datasets_pulls_lines_for_the_task_dataset() -> None:
    task = BenchmarkTask(
        id="github_repos:2", benchmark="dab", prompt="DESCRIPTION...\n\nHow many files?"
    )
    p = _build_claude_mcp_prompt(
        "main",
        _env(),
        task,
        include_cartographer_line=False,
        max_tool_calls=None,
        informed_datasets=True,
    )
    assert "actually sampled into the table" in p


def test_prompt_asks_for_the_answer_up_front_as_well_as_last() -> None:
    """2026-07-31: `stockindex:1` scored 1/5 on the Opus run — all four failures had the
    CORRECT answer on the last line, but that validator reads only `llm_output[:200]`.
    Our closing line said "on the last line", so we were instructing the model into the
    failure. Two of 104 validators read a head window; none is harmed by the answer
    appearing twice, so satisfy both."""
    p = _build_claude_mcp_prompt(
        "main", _env(), _task(), include_cartographer_line=False, max_tool_calls=None
    )
    low = p.lower()
    assert "last line" in low, "keep the existing last-line contract"
    assert "first sentence" in low or "begin" in low or "lead with" in low
