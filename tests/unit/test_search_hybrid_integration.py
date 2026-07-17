"""Hybrid-retrieval integration for search_reference_docs / search_trails (T2b v2, T4)."""

from __future__ import annotations

from pathlib import Path

import pytest

import labrat.maze.hybrid as hybrid_mod
from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.search_reference_docs import SearchReferenceDocsTool
from labrat.agent.tools.search_trails import SearchTrailsTool

_SCENT = """---
kind: scent
domain: warehouse
---
## Quick Reference
One row per order line.

## Revenue rollups
Monthly revenue rollups live in fct_revenue; join on order_id.

## Index dates
The Date column is dirty mixed-format text; parse with try_strptime.
"""

_TRAIL = """---
kind: trail
domain: monthly-finance-review
---
## When to use
Standing monthly finance review.

## Steps
Pull fct_revenue rollups, then compare month-over-month.
"""


class _KeywordEmbedder:
    """'revenue' and 'profit' share an axis — a paraphrase bridge lexical misses."""

    @property
    def model_id(self) -> str:
        return "kw-stub"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [
            [
                float(("revenue" in t.lower()) or ("profit" in t.lower())),
                float("date" in t.lower()),
                1.0,
            ]
            for t in texts
        ]


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    scent = tmp_path / "labrat_maze" / "scent"
    scent.mkdir(parents=True)
    (scent / "warehouse.md").write_text(_SCENT, encoding="utf-8")
    trail = tmp_path / "labrat_maze" / "trail"
    trail.mkdir(parents=True)
    (trail / "monthly-finance-review.md").write_text(_TRAIL, encoding="utf-8")
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


async def test_flag_off_is_byte_identical_and_never_touches_embeddings(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = SearchReferenceDocsTool()
    args = tool.input_model(question="profits chart by month")
    baseline = await tool.execute(ToolContext(profile_name="default"), args)

    def _explode() -> None:
        raise AssertionError("embedding path must not run when the flag is off")

    monkeypatch.setattr(hybrid_mod, "get_default_embedder", _explode)
    again = await tool.execute(ToolContext(profile_name="default"), args)
    assert again.model_dump_json() == baseline.model_dump_json()


async def test_flag_on_surfaces_lexically_disjoint_section(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hybrid_mod, "get_default_embedder", lambda: _KeywordEmbedder())
    tool = SearchReferenceDocsTool()
    out = await tool.execute(
        ToolContext(profile_name="default", hybrid_retrieval=True),
        # 'profits' never appears in the docs — lexical alone finds nothing.
        tool.input_model(question="profits chart"),
    )
    headings = [s.heading for r in out.results for s in r.sections]
    assert "Revenue rollups" in headings
    match = next(s for r in out.results for s in r.sections if s.heading == "Revenue rollups")
    assert match.score == 0.0 and match.matched_terms == []


async def test_flag_on_without_embedder_falls_back_to_lexical(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = SearchReferenceDocsTool()
    args = tool.input_model(question="dirty date parsing")
    baseline = await tool.execute(ToolContext(profile_name="default"), args)
    monkeypatch.setattr(hybrid_mod, "get_default_embedder", lambda: None)
    out = await tool.execute(ToolContext(profile_name="default", hybrid_retrieval=True), args)
    assert out.model_dump_json() == baseline.model_dump_json()


async def test_flag_on_empty_store_returns_no_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path / "nothing"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(hybrid_mod, "get_default_embedder", lambda: _KeywordEmbedder())
    tool = SearchReferenceDocsTool()
    out = await tool.execute(
        ToolContext(profile_name="default", hybrid_retrieval=True),
        tool.input_model(question="anything"),
    )
    assert out.results == []


async def test_search_trails_hybrid_parity(env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hybrid_mod, "get_default_embedder", lambda: _KeywordEmbedder())
    tool = SearchTrailsTool()
    out = await tool.execute(
        ToolContext(profile_name="default", hybrid_retrieval=True),
        tool.input_model(intent="profit numbers refresh"),
    )
    headings = [s.heading for r in out.results for s in r.sections]
    assert "Steps" in headings  # semantic arm bridged 'profit' → revenue Steps section
