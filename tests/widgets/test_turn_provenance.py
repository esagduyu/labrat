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


def test_structured_json_renders_domain_tier_freshness() -> None:
    prov = TurnProvenance()
    prov.record_tool(
        "search_reference_docs",
        True,
        json.dumps(
            {
                "question": "q",
                "results": [
                    {
                        "domain": "orders",
                        "quick_reference": None,
                        "sections": [],
                        "best_source": "verified",
                        "stale": False,
                    }
                ],
            }
        ),
    )
    assert (prov.footer() or "").startswith("⚑ grounded: scent: orders (verified·fresh)")


def test_structured_json_stale_and_plus_n() -> None:
    prov = TurnProvenance()
    payload = {
        "question": "q",
        "results": [
            {
                "domain": "orders",
                "quick_reference": None,
                "sections": [],
                "best_source": "harvested",
                "stale": True,
            },
            {
                "domain": "general",
                "quick_reference": None,
                "sections": [],
                "best_source": "human",
                "stale": None,
            },
        ],
    }
    prov.record_tool("search_reference_docs", True, json.dumps(payload))
    footer = prov.footer() or ""
    assert "scent: orders (harvested·stale) +1" in footer


def test_repr_shape_with_tier_fields() -> None:
    # Production in-process shape after Task 3 (build the string from the REAL model —
    # see the construction pattern in test_chat_panel.py's _GroundedFakeLoop fixture).
    from labrat.agent.tools.search_reference_docs import DocResult, SectionMatch, _Output

    out = _Output(
        question="q",
        results=[
            DocResult(
                domain="orders",
                quick_reference=None,
                # Non-empty sections matter: the nested SectionMatch(...) parens are
                # exactly what breaks naive per-DocResult regex segmentation.
                sections=[
                    SectionMatch(
                        heading="Key Tables",
                        body="- orders",
                        score=1.0,
                        matched_terms=["orders"],
                        source="verified",
                        fresh=True,
                    )
                ],
                best_source="verified",
                stale=False,
            )
        ],
    )
    prov = TurnProvenance()
    prov.record_tool("search_reference_docs", True, str(out))
    assert "scent: orders (verified·fresh)" in (prov.footer() or "")


def test_unknown_freshness_never_rendered_fresh() -> None:
    prov = TurnProvenance()
    prov.record_tool(
        "search_reference_docs",
        True,
        json.dumps(
            {
                "question": "q",
                "results": [
                    {
                        "domain": "orders",
                        "quick_reference": None,
                        "sections": [],
                        "best_source": "verified",
                        "stale": None,
                    }
                ],
            }
        ),
    )
    footer = prov.footer() or ""
    assert "scent: orders (verified)" in footer
    assert "fresh" not in footer and "stale" not in footer


def test_tierless_payload_keeps_count_fallback() -> None:
    # Old-shape JSON (no best_source key) → today's count format, global flag.
    prov = TurnProvenance(scent_stale=True)
    prov.record_tool(
        "search_reference_docs",
        True,
        json.dumps(
            {
                "question": "q",
                "results": [{"domain": "orders", "quick_reference": None, "sections": []}],
            }
        ),
    )
    assert "scent ×1 (stale)" in (prov.footer() or "")  # noqa: RUF001


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
