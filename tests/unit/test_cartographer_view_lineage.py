"""Unit D: lineage source token + build_view_lineage + generate_scent wiring + audit."""

from __future__ import annotations

from labrat.maze.document import ScentDoc, Section, parse_document, render_document


def test_lineage_source_token_round_trips() -> None:
    doc = ScentDoc(
        domain="shop",
        sections=[
            Section(
                heading="View Lineage",
                body="- view `customer_spend`.`total` ← `orders`.`amount`",
                source="lineage",
            )
        ],
    )
    rendered = render_document(doc)
    assert "**Source:** lineage" in rendered
    reparsed = parse_document(rendered, domain="shop")
    section = next(s for s in reparsed.sections if s.heading == "View Lineage")
    assert section.source == "lineage"


def test_unknown_source_token_still_falls_back_to_human() -> None:
    text = "---\ndomain: d\n---\n\n## X\n**Source:** wizardry\n\n- body\n"
    doc = parse_document(text, domain="d")
    assert doc.sections[0].source == "human"
