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


def test_unparseable_scent_output_degrades_to_count() -> None:
    prov = TurnProvenance()
    prov.record_tool("search_reference_docs", True, "result://abc/0001 (summarized)")
    assert "scent ×1" in (prov.footer() or "")  # noqa: RUF001


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
    prov2 = TurnProvenance()
    prov2.set_verifier(rounds_used=2)
    assert "verifier ✓ (2 rounds)" in (prov2.footer() or "")
    prov3 = TurnProvenance()
    prov3.set_verifier(rounds_used=None)  # verification off → no verifier segment
    assert prov3.footer() is None
