"""LLM draft pass + immutability merge for the cartographer (#26b)."""

from __future__ import annotations

from labrat.maze.cartographer import draft_semantics, merge_sections
from labrat.maze.document import ScentDoc, Section

_LLM_OUTPUT = """## Gotchas
- Revenue is total_amount; exclude is_test rows.

## Key Tables
- (the model tried to overwrite a verified section)
"""


async def _stub_llm(prompt: str) -> str:
    return _LLM_OUTPUT


async def test_draft_sections_are_tagged_draft() -> None:
    skeleton = ScentDoc(
        domain="sales",
        sections=[Section(heading="Key Tables", body="- verified facts", source="verified")],
    )
    drafted = await draft_semantics(skeleton, _stub_llm)
    by_heading = {s.heading: s for s in drafted}
    assert "Gotchas" in by_heading
    assert all(s.source == "draft" for s in drafted)


def test_merge_keeps_verified_immutable() -> None:
    verified = [Section(heading="Key Tables", body="- verified facts", source="verified")]
    drafted = [
        Section(heading="Gotchas", body="- a gotcha", source="draft"),
        Section(heading="Key Tables", body="- LLM override attempt", source="draft"),
    ]
    merged = merge_sections(verified, drafted)
    kt = [s for s in merged if s.heading == "Key Tables"]
    assert len(kt) == 1  # the draft "Key Tables" was dropped
    assert kt[0].source == "verified"
    assert kt[0].body == "- verified facts"  # untouched
    assert any(s.heading == "Gotchas" and s.source == "draft" for s in merged)
