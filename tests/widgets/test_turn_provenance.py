"""TurnProvenance: aggregate a turn's grounding signals into one footer line."""

import json

from labrat.widgets.turn_provenance import TurnProvenance


def _scent_output(domains: list[str]) -> str:
    return json.dumps(
        {
            "question": "q",
            "results": [{"domain": d, "quick_reference": None, "sections": []} for d in domains],
        }
    )


def test_empty_turn_has_no_footer() -> None:
    assert TurnProvenance().footer() is None


def test_scent_hits_counted_with_freshness() -> None:
    prov = TurnProvenance(scent_stale=False)
    prov.record_tool("search_reference_docs", True, _scent_output(["orders", "general"]))
    footer = prov.footer()
    assert footer is not None
    assert "scent ×2" in footer and "fresh" in footer  # noqa: RUF001


def test_stale_scent_labelled() -> None:
    prov = TurnProvenance(scent_stale=True)
    prov.record_tool("search_reference_docs", True, _scent_output(["orders"]))
    assert "stale" in (prov.footer() or "")


def test_empty_scent_results_are_not_grounding_evidence() -> None:
    prov = TurnProvenance()
    prov.record_tool("search_reference_docs", True, _scent_output([]))
    assert prov.footer() is None


def test_unparseable_scent_output_degrades_to_count() -> None:
    prov = TurnProvenance()
    prov.record_tool("search_reference_docs", True, "result://abc/0001 (summarized)")
    assert "scent ×1" in (prov.footer() or "")  # noqa: RUF001


def test_empty_repr_scent_output_is_not_grounding_evidence() -> None:
    """Production shape: Pydantic repr (no __str__ override), not JSON. Empty case."""
    prov = TurnProvenance()
    prov.record_tool("search_reference_docs", True, "question='q' results=[]")
    assert prov.footer() is None


def test_multi_hit_repr_scent_output_counted() -> None:
    """Production shape: Pydantic repr with two DocResult hits."""
    prov = TurnProvenance()
    output = (
        "question='q' results=[DocResult(domain='orders', quick_reference=None, "
        "sections=[SectionMatch(heading='h1', body='b1', score=1.0, matched_terms=['a'])]), "
        "DocResult(domain='users', quick_reference='qr', "
        "sections=[SectionMatch(heading='h2', body='b2', score=2.0, matched_terms=['b']), "
        "SectionMatch(heading='h3', body='b3', score=3.0, matched_terms=['c'])])]"
    )
    prov.record_tool("search_reference_docs", True, output)
    footer = prov.footer()
    assert footer is not None
    assert "scent ×2" in footer  # noqa: RUF001


def test_join_lineage_and_query_count() -> None:
    prov = TurnProvenance()
    prov.record_tool("verify_join", True, "{}")
    prov.record_tool("explain_lineage", True, "{}")
    prov.record_tool("run_sql", True, "{}")
    prov.record_tool("run_sql", True, "{}")
    footer = prov.footer() or ""
    assert "join verified" in footer
    assert "lineage" in footer
    assert "2 queries" in footer


def test_failed_calls_not_counted() -> None:
    prov = TurnProvenance()
    prov.record_tool("run_sql", False, "error")
    prov.record_tool("verify_join", False, "error")
    assert prov.footer() is None


def test_verifier_outcome() -> None:
    prov = TurnProvenance()
    prov.set_verifier(rounds_used=0)
    assert "verifier ✓" in (prov.footer() or "")
    prov1 = TurnProvenance()
    prov1.set_verifier(rounds_used=1)
    assert "verifier ✓ (1 round)" in (prov1.footer() or "")
    prov2 = TurnProvenance()
    prov2.set_verifier(rounds_used=2)
    assert "verifier ✓ (2 rounds)" in (prov2.footer() or "")
    prov3 = TurnProvenance()
    prov3.set_verifier(rounds_used=None)  # verification off → no verifier segment
    assert prov3.footer() is None
