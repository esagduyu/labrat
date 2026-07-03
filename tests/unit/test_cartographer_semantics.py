"""LLM draft pass + immutability merge for the cartographer (#26b / M2 conditional claims)."""

from __future__ import annotations

from labrat.maze.cartographer import _SEMANTICS_INSTRUCTION, draft_semantics, merge_sections
from labrat.maze.document import ScentDoc, Section

_LLM_OUTPUT = """## Gotchas
- Revenue is total_amount; exclude is_test rows.

## Key Tables
- (the model tried to overwrite a verified section)
"""


async def _stub_llm(prompt: str) -> str:
    return _LLM_OUTPUT


async def test_draft_sections_are_tagged_draft() -> None:
    # draft_semantics now returns (prose_sections, raw_claims_text); this stub LLM output
    # has no "## Semantic Claims" section so raw_claims_text is "".
    skeleton = ScentDoc(
        domain="sales",
        sections=[Section(heading="Key Tables", body="- verified facts", source="verified")],
    )
    drafted, raw_claims = await draft_semantics(skeleton, _stub_llm)
    by_heading = {s.heading: s for s in drafted}
    assert "Gotchas" in by_heading
    assert all(s.source == "draft" for s in drafted)
    assert raw_claims == ""


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


async def test_draft_returns_prose_and_claims_separately() -> None:
    async def _llm(_prompt: str) -> str:
        return (
            "## Semantic Claims\n"
            "JOIN orders.customer_id = customers.id\n\n"
            "## Gotchas\n"
            "- When the question asks for coded values, use the code column.\n"
        )

    skeleton = ScentDoc(
        domain="x", sections=[Section(heading="Key Tables", body="...", source="verified")]
    )
    prose, raw_claims = await draft_semantics(skeleton, _llm)
    assert "JOIN orders.customer_id = customers.id" in raw_claims
    assert all(s.heading.strip().lower() != "semantic claims" for s in prose)  # claims not in prose
    assert any(s.heading.strip().lower() == "gotchas" for s in prose)
    assert all(s.source == "draft" for s in prose)


def test_instruction_forbids_unconditional_rules() -> None:
    low = _SEMANTICS_INSTRUCTION.lower()
    assert "conditional" in low and "semantic claims" in low
