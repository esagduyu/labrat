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
