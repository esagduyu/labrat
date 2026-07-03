# tests/unit/test_semantic_claims.py
from __future__ import annotations

from labrat.maze.semantic_claims import JoinClaim, RoleClaim, parse_semantic_claims


def test_parses_join_and_role_lines() -> None:
    text = (
        "JOIN orders.customer_id = customers.id\n"
        "ROLE clinical.icd_o_3_histology CODES clinical.histological_type\n"
        "this is prose, ignore it\n"
        "JOIN malformed line\n"
    )
    claims = parse_semantic_claims(text)
    assert (
        JoinClaim(
            left_table="orders", left_col="customer_id", right_table="customers", right_col="id"
        )
        in claims
    )
    assert (
        RoleClaim(table="clinical", code_col="icd_o_3_histology", name_col="histological_type")
        in claims
    )
    assert len(claims) == 2  # garbage + malformed ignored


def test_empty_text_no_claims() -> None:
    assert parse_semantic_claims("") == []
