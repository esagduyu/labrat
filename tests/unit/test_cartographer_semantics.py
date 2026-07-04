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


def test_instruction_has_cohort_vs_filter_rule() -> None:
    assert "the numerator" in _SEMANTICS_INSTRUCTION
    assert "cohort denominator (the population)" in _SEMANTICS_INSTRUCTION


async def test_draft_semantics_reroutes_heading_drift_and_stray_claim_lines() -> None:
    # FIX 2 (IMPORTANT-3): heading drift ("## Semantic Claims:" with trailing colon) plus
    # a claim-shaped line stray inside ## Gotchas must both route into raw_claims, and the
    # stray line must be stripped out of the surviving prose body.
    async def _llm(_prompt: str) -> str:
        return (
            "## Semantic Claims:\n"
            "JOIN orders.customer_id = customers.id\n\n"
            "## Gotchas\n"
            "ROLE t.a CODES t.b\n"
            "- When the question asks for coded values, use the code column.\n"
        )

    skeleton = ScentDoc(
        domain="x", sections=[Section(heading="Key Tables", body="...", source="verified")]
    )
    prose, raw_claims = await draft_semantics(skeleton, _llm)
    assert "JOIN orders.customer_id = customers.id" in raw_claims
    assert "ROLE t.a CODES t.b" in raw_claims
    gotchas = next(s for s in prose if s.heading.strip().lower() == "gotchas")
    assert "ROLE t.a CODES t.b" not in gotchas.body
    assert "When the question asks for coded values, use the code column." in gotchas.body


def test_merge_sections_reserves_verified_semantics_heading_from_spoofing() -> None:
    # FIX 4 (folded Minor): even when verify_semantic_claims returns no real verified
    # section, an LLM-authored "## Verified Semantics" prose section must be dropped, not
    # merged in draft-tagged (spoofing the verified-looking heading).
    verified: list[Section] = []  # no real verified claims survived
    drafted = [
        Section(heading="Verified Semantics", body="- trust me, bro", source="draft"),
        Section(heading="Gotchas", body="- a real gotcha", source="draft"),
    ]
    merged = merge_sections(verified, drafted)
    assert not any(
        s.heading.strip().lower() == "verified semantics" and s.source == "draft" for s in merged
    )
    assert any(s.heading == "Gotchas" and s.source == "draft" for s in merged)


async def test_generate_scent_persists_only_verified_claims(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import duckdb

    from labrat.db.duckdb_engine import DuckDBConnection
    from labrat.maze.cartographer import generate_scent

    p = str(tmp_path / "g.duckdb")
    raw = duckdb.connect(p)
    raw.execute("CREATE TABLE customers(id INT, name VARCHAR)")
    raw.execute("INSERT INTO customers VALUES (1,'a'),(2,'b')")
    raw.execute("CREATE TABLE orders(customer_id INT, amt INT)")
    raw.execute("INSERT INTO orders VALUES (1,10),(2,20)")
    raw.close()
    conn = DuckDBConnection(path=p, read_only=False)
    conn.connect()

    async def _llm(_prompt: str) -> str:
        return (
            "## Semantic Claims\n"
            "JOIN orders.customer_id = customers.id\n"  # real → survives
            "JOIN orders.amt = customers.id\n\n"  # bogus → dropped
            "## Gotchas\n- When joining orders to customers, use customer_id.\n"
        )

    docs = await generate_scent(
        connections={"main": conn},
        catalogs={"main": conn.introspect_catalog()},
        primary="main",
        with_semantics=True,
        llm_fn=_llm,
    )
    body = "\n".join(s.body for s in docs[0].sections)
    assert "orders.customer_id = customers.id" in body  # verified claim persisted
    assert "orders.amt = customers.id" not in body  # bogus claim dropped
    assert any(
        s.heading == "Verified Semantics" and s.source == "verified" for s in docs[0].sections
    )
    assert any(
        s.heading.strip().lower() == "gotchas" and s.source == "draft" for s in docs[0].sections
    )
    conn.disconnect()
