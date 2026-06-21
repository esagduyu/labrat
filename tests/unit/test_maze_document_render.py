"""Tests for Section.source provenance + render_document round-trip (#26b)."""

from __future__ import annotations

from labrat.maze.document import ScentDoc, Section, parse_document, render_document


def test_section_source_defaults_to_human_when_unmarked() -> None:
    doc = parse_document("## Gotchas\n- something", domain="x")
    assert doc.sections[0].source == "human"


def test_parse_lifts_source_marker_out_of_body() -> None:
    doc = parse_document("## Key Tables\n**Source:** verified\n\n- orders ...", domain="x")
    s = doc.sections[0]
    assert s.source == "verified"
    assert "**Source:**" not in s.body  # marker removed from body
    assert s.body.strip() == "- orders ..."


def test_unrecognized_source_token_falls_back_to_human() -> None:
    doc = parse_document("## Gotchas\n**Source:** robot\n\nbody", domain="x")
    assert doc.sections[0].source == "human"


def test_render_then_parse_round_trips_sections_and_sources() -> None:
    doc = ScentDoc(
        domain="sales",
        kind="scent",
        tables=["orders", "customers"],
        confidence="draft",
        sections=[
            Section(heading="Quick Reference", body="2 tables.", source="verified"),
            Section(heading="Gotchas", body="- watch out", source="draft"),
        ],
    )
    reparsed = parse_document(render_document(doc), domain="sales")
    assert reparsed.domain == "sales"
    assert reparsed.kind == "scent"
    assert reparsed.tables == ["orders", "customers"]
    assert reparsed.confidence == "draft"
    got = [(s.heading, s.body, s.source) for s in reparsed.sections]
    assert got == [
        ("Quick Reference", "2 tables.", "verified"),
        ("Gotchas", "- watch out", "draft"),
    ]


def test_existing_unmarked_doc_body_is_unchanged() -> None:
    """A #26a hand-authored doc without markers parses with body intact."""
    doc = parse_document("## Gotchas\n- a\n- b", domain="x")
    assert doc.sections[0].body == "- a\n- b"
