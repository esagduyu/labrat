"""Tests for the Maze reference-doc parser + model (FEATURE_ROADMAP #26a)."""

from __future__ import annotations

from labrat.maze.document import ScentDoc, parse_document

_DOC = """---
kind: scent
domain: ecommerce_sales
tables: [orders, customers]
confidence: verified
---
Intro preamble before any heading.

## Quick Reference
Grain: one row per order line.

## Gotchas
- Dates are dirty mixed-format text; parse before any date math.
"""


def test_parses_frontmatter_and_sections() -> None:
    doc = parse_document(_DOC, domain="fallback", scope="project")
    assert doc.kind == "scent"
    assert doc.domain == "ecommerce_sales"  # frontmatter wins over the fallback
    assert doc.tables == ["orders", "customers"]
    assert doc.confidence == "verified"
    assert doc.scope == "project"
    headings = [s.heading for s in doc.sections]
    assert headings == ["", "Quick Reference", "Gotchas"]  # "" is the preamble
    qr = doc.quick_reference()
    assert qr is not None
    assert "one row per order line" in qr.body


def test_missing_frontmatter_loads_body_only_with_fallback_domain() -> None:
    doc = parse_document("## Gotchas\n- something", domain="sales", scope="user")
    assert doc.domain == "sales"  # falls back to the filename stem
    assert doc.kind == "scent"
    assert [s.heading for s in doc.sections] == ["Gotchas"]


def test_malformed_frontmatter_does_not_crash() -> None:
    bad = "---\n: : not yaml : :\n---\n## Notes\nbody"
    doc = parse_document(bad, domain="x")
    assert isinstance(doc, ScentDoc)
    assert [s.heading for s in doc.sections] == ["Notes"]


def test_h3_is_not_treated_as_a_section_boundary() -> None:
    doc = parse_document("## Key Tables\n### orders\ngrain stuff", domain="x")
    assert [s.heading for s in doc.sections] == ["Key Tables"]
    assert "### orders" in doc.sections[0].body


def test_section_metadata_round_trips() -> None:
    from labrat.maze.document import ScentDoc, Section, parse_document, render_document

    doc = ScentDoc(
        domain="sales",
        tables=["orders"],
        sections=[
            Section(
                heading="Gotchas",
                body="- Orders can be soft-deleted.",
                source="harvested",
                generated_at="2026-07-06T00:00:00Z",
                schema_hash="abc123",
                model_id="claude-sonnet-4-6",
            )
        ],
    )
    text = render_document(doc)
    reparsed = parse_document(text, domain="sales")
    s = next(s for s in reparsed.sections if s.heading == "Gotchas")
    assert s.source == "harvested"
    assert s.generated_at == "2026-07-06T00:00:00Z"
    assert s.schema_hash == "abc123"
    assert s.model_id == "claude-sonnet-4-6"


def test_legacy_doc_without_meta_still_parses() -> None:
    from labrat.maze.document import parse_document

    legacy = "---\ndomain: x\nkind: scent\n---\n\n## Gotchas\n**Source:** verified\n\n- Note.\n"
    doc = parse_document(legacy, domain="x")
    s = next(s for s in doc.sections if s.heading == "Gotchas")
    assert s.source == "verified"
    assert s.generated_at is None and s.schema_hash is None
