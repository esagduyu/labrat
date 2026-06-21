"""The shipped Scent example parses and is retrievable end-to-end (FEATURE_ROADMAP #26a)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from labrat.agent.tools.base import ToolContext
from labrat.agent.tools.search_reference_docs import SearchReferenceDocsTool
from labrat.maze.document import parse_document


def test_template_and_example_files_exist() -> None:
    assert Path("docs/scent/TEMPLATE.md").is_file()
    assert Path("docs/scent/examples/ecommerce_sales.md").is_file()


def test_example_parses_with_template_sections() -> None:
    doc = parse_document(
        Path("docs/scent/examples/ecommerce_sales.md").read_text(encoding="utf-8"),
        domain="ecommerce_sales",
    )
    assert doc.kind == "scent"
    headings = {s.heading for s in doc.sections}
    assert {"Quick Reference", "Key Tables", "Gotchas"} <= headings


async def test_example_is_retrievable_through_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scent = tmp_path / "labrat_maze" / "scent"
    scent.mkdir(parents=True)
    shutil.copy("docs/scent/examples/ecommerce_sales.md", scent / "ecommerce_sales.md")
    monkeypatch.setenv("LABRAT_MAZE_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "empty_home"))

    tool = SearchReferenceDocsTool()
    out = await tool.execute(
        ToolContext(profile_name="default"),
        tool.input_model(question="how do I join orders to customers?"),
    )
    assert any(r.domain == "ecommerce_sales" for r in out.results)
