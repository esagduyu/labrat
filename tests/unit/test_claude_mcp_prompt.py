"""The claude-mcp opening-prompt builder (extracted for the leaderboard prompt audit)."""

from __future__ import annotations

from labrat.agent.tools.base import ToolContext
from labrat.db.catalog import Catalog
from labrat.eval.benchmarks.dab.env import DabTaskEnv
from labrat.eval.benchmarks.dab.suite import _build_claude_mcp_prompt
from labrat.eval.types import BenchmarkTask


def _env() -> DabTaskEnv:
    ctx = ToolContext(
        connections={"main": object()},
        catalogs={"main": Catalog(database_name="main", schemas=[])},
        primary="main",
    )
    return DabTaskEnv(ctx=ctx, attachable=[], mongo=[])


def _task() -> BenchmarkTask:
    return BenchmarkTask(id="demo:1", benchmark="dab", prompt="DESCRIPTION...\n\nHow many rows?")


def test_prompt_contains_question_and_levers() -> None:
    p = _build_claude_mcp_prompt(
        "main", _env(), _task(), include_cartographer_line=False, max_tool_calls=None
    )
    assert "How many rows?" in p  # the task (description + question) is embedded
    assert "never answer from prior" in p  # a process lever
    assert "respond with the final answer on the last line" in p
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
