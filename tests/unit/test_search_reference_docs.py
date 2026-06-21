"""Tests for the search_reference_docs tool (FEATURE_ROADMAP #26a, Scent consume half)."""

from __future__ import annotations

from pathlib import Path

import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.search_reference_docs import SearchReferenceDocsTool

_STOCKINDEX = """---
kind: scent
domain: stockindex
---
## Quick Reference
One row per index per day. Use CloseUSD for cross-country comparisons.

## Gotchas
- The Date column is dirty mixed-format text; parse with try_strptime before any date math.

## Best Practices
- Prefer adjusted close.
"""


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    scent = tmp_path / "labrat_maze" / "scent"
    scent.mkdir(parents=True)
    (scent / "stockindex.md").write_text(_STOCKINDEX, encoding="utf-8")
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "empty_home"))
    return tmp_path


async def test_returns_matching_section_with_quick_reference(env: Path) -> None:
    tool = SearchReferenceDocsTool()
    out = await tool.execute(
        ToolContext(profile_name="default"),
        tool.input_model(question="average return by country, the dates look wrong"),
    )
    assert len(out.results) == 1
    res = out.results[0]
    assert res.domain == "stockindex"
    # the Gotchas section (matches "dates"/"date") is returned
    headings = [s.heading for s in res.sections]
    assert "Gotchas" in headings
    # Quick Reference is prepended for context (it was not itself a hit here)
    assert res.quick_reference is not None
    assert "CloseUSD" in res.quick_reference


async def test_empty_store_is_a_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Benchmark-safety guarantee: no docs -> no results (never falls back to all)."""
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path / "nothing"))
    monkeypatch.setenv("HOME", str(tmp_path / "empty_home"))
    tool = SearchReferenceDocsTool()
    out = await tool.execute(
        ToolContext(profile_name="default"),
        tool.input_model(question="anything at all"),
    )
    assert out.results == []


async def test_no_lexical_match_returns_empty(env: Path) -> None:
    tool = SearchReferenceDocsTool()
    out = await tool.execute(
        ToolContext(profile_name="default"),
        tool.input_model(question="kubernetes pod autoscaling latency"),
    )
    assert out.results == []


async def test_top_k_caps_matched_sections(env: Path) -> None:
    tool = SearchReferenceDocsTool()
    out = await tool.execute(
        ToolContext(profile_name="default"),
        tool.input_model(question="index date close adjusted practices", top_k=1),
    )
    total_sections = sum(len(r.sections) for r in out.results)
    assert total_sections == 1


async def test_top_k_caps_total_sections_across_docs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """top_k caps the total matched sections across docs, not per-doc."""
    scent = tmp_path / "labrat_maze" / "scent"
    scent.mkdir(parents=True)
    (scent / "a.md").write_text(
        "---\nkind: scent\ndomain: alpha\n---\n## Gotchas\n- alpha date parsing note\n",
        encoding="utf-8",
    )
    (scent / "b.md").write_text(
        "---\nkind: scent\ndomain: beta\n---\n## Gotchas\n- beta date parsing note\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "empty_home"))

    tool = SearchReferenceDocsTool()
    out = await tool.execute(
        ToolContext(profile_name="default"),
        tool.input_model(question="date parsing", top_k=1),
    )
    total = sum(len(r.sections) for r in out.results)
    assert total == 1  # both docs' Gotchas match "date parsing"; cap is across docs
